"""Name resolution: getaddrinfo, rendered back to text.

The answers come back as text rather than as a typed address, because a .bc
module cannot build a Sushi enum: <net/dns> parses each one with <net/ip>. The
rendering is getnameinfo with NI_NUMERICHOST, which asks no resolver of its
own, so this walk makes exactly one network request.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_malloc,
    declare_realloc,
    declare_strlen,
)
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.net.errno import (
    NET_ERROR_RESOLVE_FAILED,
    emit_errno_err_result,
)
from sushi_lang.sushi_stdlib.src.results import emit_err_result, emit_ok_result
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_dynamic_array_type,
    get_result_type,
    get_string_type,
    get_unit_enum_type,
)

# How many answers the first allocation holds. A host with more than this many
# addresses grows the array; most have one or two.
_INITIAL_CAPACITY = 4


def generate_ir(module: ir.Module) -> None:
    """Emit every DNS symbol into the module."""
    generate_resolve(module)


def generate_resolve(module: ir.Module) -> None:
    """Emit `Result<{i32,i32,string*}, NetError> sushi_net_sock_dns_resolve(i8*)`.

    An answer getnameinfo cannot render is SKIPPED rather than failing the
    call, so one unusual family does not lose the usable answers. That is also
    what makes the element array safe: there is no error path after it is
    allocated, so it can never be orphaned.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getaddrinfo_fn = platform_net.declare_getaddrinfo(module)
    freeaddrinfo_fn = platform_net.declare_freeaddrinfo(module)
    getnameinfo_fn = platform_net.declare_getnameinfo(module)
    malloc_fn = declare_malloc(module)
    realloc_fn = declare_realloc(module)
    strlen_fn = declare_strlen(module)

    string_ty = get_string_type()
    array_ty = get_dynamic_array_type(string_ty)
    result_type = get_result_type(array_ty, get_unit_enum_type())

    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr]),
                       name="sushi_net_sock_dns_resolve")
    func.args[0].name = "host"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    zero = ir.Constant(i32, 0)
    null = ir.Constant(i8_ptr, None)
    string_stride = ir.Constant(i64, 16)

    # Every alloca is here, in the entry block. One inside the walk below would
    # grow the frame per answer, and the host buffer is a kilobyte each time.
    res_slot = builder.alloca(i8_ptr, name="res_slot")
    cur_slot = builder.alloca(i8_ptr, name="cur_slot")
    len_slot = builder.alloca(i32, name="len_slot")
    cap_slot = builder.alloca(i32, name="cap_slot")
    data_slot = builder.alloca(string_ty.as_pointer(), name="data_slot")
    host_buf = addr.alloca_zeroed(builder, platform_net.NI_MAXHOST, "host_buf")

    hints = addr.emit_hints(builder, platform_net.SOCK_STREAM, passive=False)
    rc = builder.call(getaddrinfo_fn, [func.args[0], null, hints, res_slot],
                      name="gai_rc")

    gai_ok_bb = func.append_basic_block(name="gai_ok")
    gai_fail_bb = func.append_basic_block(name="gai_fail")
    builder.cbranch(builder.icmp_signed("==", rc, zero, name="gai_ok_p"),
                    gai_ok_bb, gai_fail_bb)

    builder.position_at_end(gai_fail_bb)
    gai_sys_bb = func.append_basic_block(name="gai_errno")
    gai_perm_bb = func.append_basic_block(name="gai_resolve_failed")
    builder.cbranch(
        builder.icmp_signed("==", rc, ir.Constant(i32, platform_net.EAI_SYSTEM),
                            name="gai_is_system"),
        gai_sys_bb, gai_perm_bb)

    builder.position_at_end(gai_sys_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(gai_perm_bb)
    builder.ret(emit_err_result(builder, result_type,
                                ir.Constant(i32, NET_ERROR_RESOLVE_FAILED)))

    builder.position_at_end(gai_ok_bb)
    initial_bytes = builder.mul(ir.Constant(i64, _INITIAL_CAPACITY), string_stride,
                                name="initial_bytes")
    buffer = builder.call(malloc_fn, [initial_bytes], name="answers")
    builder.store(builder.bitcast(buffer, string_ty.as_pointer()), data_slot)
    builder.store(zero, len_slot)
    builder.store(ir.Constant(i32, _INITIAL_CAPACITY), cap_slot)
    builder.store(builder.load(res_slot, name="res"), cur_slot)

    loop_bb = func.append_basic_block(name="loop_head")
    builder.branch(loop_bb)

    render_bb = func.append_basic_block(name="render")
    done_bb = func.append_basic_block(name="done")
    builder.position_at_end(loop_bb)
    cur = builder.load(cur_slot, name="cur")
    builder.cbranch(builder.icmp_unsigned("==", cur, null, name="at_end"),
                    done_bb, render_bb)

    advance_bb = func.append_basic_block(name="advance")
    keep_bb = func.append_basic_block(name="keep")

    builder.position_at_end(render_bb)
    cur = builder.load(cur_slot, name="cur_render")
    sockaddr = addr.load_ptr_at(builder, cur, platform_net.AI_ADDR_OFFSET, "ai_addr")
    salen = addr.load_i32_at(builder, cur, platform_net.AI_ADDRLEN_OFFSET, "ai_addrlen")
    rc = builder.call(getnameinfo_fn, [
        sockaddr, salen,
        host_buf, ir.Constant(i32, platform_net.NI_MAXHOST),
        null, zero,
        ir.Constant(i32, platform_net.NI_NUMERICHOST),
    ], name="render_rc")
    builder.cbranch(builder.icmp_signed("==", rc, zero, name="rendered"),
                    keep_bb, advance_bb)

    grow_bb = func.append_basic_block(name="grow")
    store_bb = func.append_basic_block(name="store")
    builder.position_at_end(keep_bb)
    cur_len = builder.load(len_slot, name="cur_len")
    cur_cap = builder.load(cap_slot, name="cur_cap")
    builder.cbranch(builder.icmp_signed("==", cur_len, cur_cap, name="is_full"),
                    grow_bb, store_bb)

    builder.position_at_end(grow_bb)
    new_cap = builder.mul(builder.load(cap_slot, name="cap_to_grow"),
                          ir.Constant(i32, 2), name="new_cap")
    new_bytes = builder.mul(builder.zext(new_cap, i64, name="new_cap64"),
                            string_stride, name="new_bytes")
    grown = builder.call(realloc_fn, [
        builder.bitcast(builder.load(data_slot, name="data_to_grow"), i8_ptr),
        new_bytes,
    ], name="grown")
    builder.store(builder.bitcast(grown, string_ty.as_pointer()), data_slot)
    builder.store(new_cap, cap_slot)
    builder.branch(store_bb)

    builder.position_at_end(store_bb)
    length = builder.call(strlen_fn, [host_buf], name="answer_len")
    length64 = builder.zext(length, i64, name="answer_len64")
    owned = builder.call(malloc_fn, [length64], name="answer_buf")
    memcpy_fn = builder.module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])
    builder.call(memcpy_fn, [owned, host_buf, length64, ir.Constant(ir.IntType(1), 0)])
    text = cstr_to_fat_pointer_with_len(builder, owned, length, owned=1)

    slot_index = builder.load(len_slot, name="slot_index")
    data = builder.load(data_slot, name="data")
    builder.store(text, builder.gep(data, [slot_index], name="slot"))
    builder.store(builder.add(slot_index, ir.Constant(i32, 1), name="next_len"), len_slot)
    builder.branch(advance_bb)

    builder.position_at_end(advance_bb)
    cur = builder.load(cur_slot, name="cur_advance")
    nxt = addr.load_ptr_at(builder, cur, platform_net.AI_NEXT_OFFSET, "ai_next")
    builder.store(nxt, cur_slot)
    builder.branch(loop_bb)

    builder.position_at_end(done_bb)
    builder.call(freeaddrinfo_fn, [builder.load(res_slot, name="res_free")])
    descriptor = ir.Constant(array_ty, ir.Undefined)
    descriptor = builder.insert_value(descriptor, builder.load(len_slot, name="final_len"),
                                      0, name="desc_len")
    descriptor = builder.insert_value(descriptor, builder.load(cap_slot, name="final_cap"),
                                      1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, builder.load(data_slot, name="final_data"),
                                      2, name="descriptor")
    builder.ret(emit_ok_result(builder, result_type, descriptor, 16))
