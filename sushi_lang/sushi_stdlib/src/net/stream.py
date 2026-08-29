"""Operations on a socket descriptor, whatever transport made it."""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_free, declare_malloc
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error
from sushi_lang.sushi_stdlib.src.net.errno import (
    emit_errno_err_result,
    emit_net_error_tag,
)
from sushi_lang.sushi_stdlib.src.results import emit_err_result, emit_ok_result
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_byte_array_type,
    get_result_type,
    get_unit_enum_type,
)


def generate_ir(module: ir.Module) -> None:
    """Emit every descriptor-level symbol into the module."""
    generate_close(module)
    generate_local_port(module)
    generate_send(module)
    generate_recv(module)


def generate_close(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_close(i32 fd)`.

    close() on a descriptor that was never open sets EBADF, which the table
    maps to NetError.Closed -- the condition, rather than a fall-through to
    Other.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    close_fn = platform_net.declare_close(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_close")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    rc = builder.call(close_fn, [func.args[0]], name="close_rc")
    zero = ir.Constant(i32, 0)
    ok = builder.icmp_signed("==", rc, zero, name="close_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, zero, 4))


def generate_local_port(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_local_port(i32 fd)`.

    getsockname, not a remembered argument: a bind to port 0 asks the kernel to
    choose, and this is the only way to learn what it chose.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getsockname_fn = platform_net.declare_getsockname(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_local_port")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    storage = addr.alloca_zeroed(builder, platform_net.SOCKADDR_STORAGE_SIZE, "ss")
    len_slot = builder.alloca(i32, name="ss_len")
    builder.store(ir.Constant(i32, platform_net.SOCKADDR_STORAGE_SIZE), len_slot)

    rc = builder.call(getsockname_fn, [func.args[0], storage, len_slot],
                      name="getsockname_rc")
    ok = builder.icmp_signed("==", rc, ir.Constant(i32, 0), name="getsockname_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               addr.emit_read_port(builder, storage), 4))


def generate_send(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_send(i32 fd, {i32,i32,i8*} data)`.

    The descriptor arrives BY VALUE and the buffer behind it belongs to the
    caller: this never frees it, and a caller may read the array again after
    the call. That is the ordinary borrow every unmarked stdlib parameter gets.

    One send is one write. It may take fewer bytes than were offered, and the
    count it took is the Ok value; looping until the whole buffer is gone is
    the caller's job.

    SEND_FLAGS carries MSG_NOSIGNAL on Linux, which is how a write to a dead
    peer answers EPIPE instead of killing the process. macOS has SO_NOSIGPIPE
    on the socket instead, so its SEND_FLAGS is zero.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    send_fn = platform_net.declare_send(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, array_ty]),
                       name="sushi_net_sock_send")
    fd, data = func.args
    fd.name, data.name = "fd", "data"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    length = builder.extract_value(data, 0, name="data_len")
    buffer = builder.extract_value(data, 2, name="data_ptr")
    length64 = builder.zext(length, i64, name="data_len64")

    sent = builder.call(send_fn, [
        fd, buffer, length64, ir.Constant(i32, platform_net.SEND_FLAGS),
    ], name="sent")
    ok = builder.icmp_signed(">=", sent, ir.Constant(i64, 0), name="send_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               builder.trunc(sent, i32, name="sent32"), 4))


def generate_recv(module: ir.Module) -> None:
    """Emit `Result<{i32,i32,i8*}, NetError> sushi_net_sock_recv(i32 fd, i32 max)`.

    The answer is one read, not `max` bytes: len carries what arrived and cap
    stays max, so the array destructor frees the whole buffer whatever len says
    and a later push reuses the slack.

    A recv of zero bytes is a clean close and answers Ok with an EMPTY array,
    not an error. recv sets no errno there, so an Err would report whatever
    errno happened to hold. It is unambiguous because a timeout answers -1 with
    EAGAIN, which becomes Err(TimedOut) -- so `while data.len() > 0` is a
    correct read loop, and NetError.Closed is what a WRITE to a dead peer gets.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    recv_fn = platform_net.declare_recv(module)
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(array_ty, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i32]),
                       name="sushi_net_sock_recv")
    fd, maximum = func.args
    fd.name, maximum.name = "fd", "max"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    max64 = builder.zext(maximum, i64, name="max64")
    buffer = builder.call(malloc_fn, [max64], name="recv_buf")
    is_null = builder.icmp_unsigned("==", buffer, ir.Constant(i8_ptr, None),
                                    name="alloc_failed")

    alloc_fail_bb = func.append_basic_block(name="alloc_fail")
    do_recv_bb = func.append_basic_block(name="do_recv")
    builder.cbranch(is_null, alloc_fail_bb, do_recv_bb)

    builder.position_at_end(alloc_fail_bb)
    emit_runtime_error(module, builder, "RE2021")

    builder.position_at_end(do_recv_bb)
    got = builder.call(recv_fn, [fd, buffer, max64, ir.Constant(i32, 0)], name="got")
    ok = builder.icmp_signed(">=", got, ir.Constant(i64, 0), name="recv_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    # The tag before the free: free() overwrites errno.
    builder.position_at_end(failure_bb)
    tag = emit_net_error_tag(builder, module)
    builder.call(free_fn, [buffer])
    builder.ret(emit_err_result(builder, result_type, tag))

    builder.position_at_end(success_bb)
    descriptor = ir.Constant(array_ty, ir.Undefined)
    descriptor = builder.insert_value(descriptor,
                                      builder.trunc(got, i32, name="got32"), 0,
                                      name="desc_len")
    descriptor = builder.insert_value(descriptor, maximum, 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, buffer, 2, name="descriptor")
    builder.ret(emit_ok_result(builder, result_type, descriptor, 16))
