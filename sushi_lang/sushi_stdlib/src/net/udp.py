"""UDP: bind, send to a peer, receive from one.

The datagram half needs one thing the stream half does not. A connected socket
answers getpeername at any time, so sock_peer_ip is a separate call; an
UNCONNECTED datagram socket has no peer at all, and the sender exists only at
the instant its datagram arrives. So udp_recv_from answers with a Datagram --
the bytes and the sender together -- rather than with bytes alone.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_free,
    declare_malloc,
    declare_strlen,
)
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.net.errno import (
    NET_ERROR_RESOLVE_FAILED,
    emit_errno_err_result,
    emit_net_error_tag,
)
from sushi_lang.sushi_stdlib.src.net.tcp import _emit_addrinfo_walk
from sushi_lang.sushi_stdlib.src.results import emit_err_result, emit_ok_result
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_byte_array_type,
    get_datagram_result_type,
    get_datagram_type,
    get_result_type,
    get_unit_enum_type,
)


def generate_ir(module: ir.Module) -> None:
    """Emit every UDP symbol into the module."""
    generate_bind(module)
    generate_send_to(module)
    generate_recv_from(module)


def generate_bind(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_udp_bind(i8*, i32)`.

    The same resolve-and-walk as a TCP listener, with SOCK_DGRAM and no
    listen(). SO_REUSEADDR is NOT set: on a datagram socket it means something
    else -- several sockets sharing a port -- and nobody asked for that.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr, i32]),
                       name="sushi_net_sock_udp_bind")
    host, port = func.args
    host.name, port.name = "host", "port"

    builder = ir.IRBuilder(func.append_basic_block(name="entry"))
    one_slot = addr.alloca_one_i32(builder, "opt_on")
    bind_fn = platform_net.declare_bind(module)

    def use_candidate(b, fd, sockaddr, salen, on_success, on_failure):
        addr.emit_patch_port(b, sockaddr, port)
        rc = b.call(bind_fn, [fd, sockaddr, salen], name="bind_rc")
        b.cbranch(b.icmp_signed("==", rc, ir.Constant(i32, 0), name="bind_ok"),
                  on_success, on_failure)

    _emit_addrinfo_walk(builder, func, module, result_type, host,
                        platform_net.SOCK_DGRAM, passive=True,
                        one_slot=one_slot, use_candidate=use_candidate)


def generate_send_to(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_udp_send_to(i32, bytes, i8*, i32)`.

    The destination is resolved per call. That is the cost of a stateless API
    and it is what a caller sending to many peers from one socket wants.

    The buffer belongs to the caller, exactly as it does for sock_send.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getaddrinfo_fn = platform_net.declare_getaddrinfo(module)
    freeaddrinfo_fn = platform_net.declare_freeaddrinfo(module)
    sendto_fn = platform_net.declare_sendto(module)

    array_ty = get_byte_array_type()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module,
                       ir.FunctionType(result_type, [i32, array_ty, i8_ptr, i32]),
                       name="sushi_net_sock_udp_send_to")
    fd, data, host, port = func.args
    fd.name, data.name, host.name, port.name = "fd", "data", "host", "port"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    zero = ir.Constant(i32, 0)
    null = ir.Constant(i8_ptr, None)
    res_slot = builder.alloca(i8_ptr, name="res_slot")

    hints = addr.emit_hints(builder, platform_net.SOCK_DGRAM, passive=False)
    rc = builder.call(getaddrinfo_fn, [host, null, hints, res_slot], name="gai_rc")

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
    res = builder.load(res_slot, name="res")
    sockaddr = addr.load_ptr_at(builder, res, platform_net.AI_ADDR_OFFSET, "ai_addr")
    salen = addr.load_i32_at(builder, res, platform_net.AI_ADDRLEN_OFFSET, "ai_addrlen")
    addr.emit_patch_port(builder, sockaddr, port)

    length = builder.extract_value(data, 0, name="data_len")
    buffer = builder.extract_value(data, 2, name="data_ptr")
    length64 = builder.zext(length, i64, name="data_len64")
    sent = builder.call(sendto_fn, [
        fd, buffer, length64, ir.Constant(i32, platform_net.SEND_FLAGS),
        sockaddr, salen,
    ], name="sent")

    # The tag before freeaddrinfo, which overwrites errno.
    tag = emit_net_error_tag(builder, module)
    builder.call(freeaddrinfo_fn, [builder.load(res_slot, name="res_free")])
    ok = builder.icmp_signed(">=", sent, ir.Constant(i64, 0), name="send_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_err_result(builder, result_type, tag))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               builder.trunc(sent, i32, name="sent32"), 4))


def generate_recv_from(module: ir.Module) -> None:
    """Emit `Result<Datagram, NetError> sushi_net_sock_udp_recv_from(i32, i32)`.

    The sender is rendered with getnameinfo and NI_NUMERICHOST, which asks no
    resolver. A sender that cannot be rendered leaves peer_ip empty rather than
    failing the receive: the bytes did arrive, and they are what was asked for.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    recvfrom_fn = platform_net.declare_recvfrom(module)
    getnameinfo_fn = platform_net.declare_getnameinfo(module)
    malloc_fn = declare_malloc(module)
    free_fn = declare_free(module)
    strlen_fn = declare_strlen(module)

    array_ty = get_byte_array_type()
    datagram_ty = get_datagram_type()
    result_type = get_datagram_result_type()
    func = ir.Function(module, ir.FunctionType(result_type, [i32, i32]),
                       name="sushi_net_sock_udp_recv_from")
    fd, maximum = func.args
    fd.name, maximum.name = "fd", "max"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    zero = ir.Constant(i32, 0)
    null = ir.Constant(i8_ptr, None)

    storage = addr.alloca_zeroed(builder, platform_net.SOCKADDR_STORAGE_SIZE, "ss")
    len_slot = builder.alloca(i32, name="ss_len")
    builder.store(ir.Constant(i32, platform_net.SOCKADDR_STORAGE_SIZE), len_slot)
    host_buf = addr.alloca_zeroed(builder, platform_net.NI_MAXHOST, "host_buf")
    ip_slot = builder.alloca(i8_ptr, name="ip_slot")
    ip_len_slot = builder.alloca(i32, name="ip_len_slot")

    max64 = builder.zext(maximum, i64, name="max64")
    buffer = builder.call(malloc_fn, [max64], name="recv_buf")
    is_null = builder.icmp_unsigned("==", buffer, null, name="alloc_failed")

    alloc_fail_bb = func.append_basic_block(name="alloc_fail")
    do_recv_bb = func.append_basic_block(name="do_recv")
    builder.cbranch(is_null, alloc_fail_bb, do_recv_bb)

    builder.position_at_end(alloc_fail_bb)
    emit_runtime_error(module, builder, "RE2021")

    builder.position_at_end(do_recv_bb)
    got = builder.call(recvfrom_fn, [
        fd, buffer, max64, zero, storage, len_slot,
    ], name="got")
    ok = builder.icmp_signed(">=", got, ir.Constant(i64, 0), name="recv_ok")

    named_bb = func.append_basic_block(name="have_datagram")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, named_bb, failure_bb)

    # The tag before the free: free() overwrites errno.
    builder.position_at_end(failure_bb)
    tag = emit_net_error_tag(builder, module)
    builder.call(free_fn, [buffer])
    builder.ret(emit_err_result(builder, result_type, tag))

    builder.position_at_end(named_bb)
    salen = builder.load(len_slot, name="ss_len_value")
    rc = builder.call(getnameinfo_fn, [
        storage, salen,
        host_buf, ir.Constant(i32, platform_net.NI_MAXHOST),
        null, zero,
        ir.Constant(i32, platform_net.NI_NUMERICHOST),
    ], name="render_rc")

    rendered_bb = func.append_basic_block(name="rendered")
    anonymous_bb = func.append_basic_block(name="anonymous")
    build_bb = func.append_basic_block(name="build")
    builder.cbranch(builder.icmp_signed("==", rc, zero, name="render_ok"),
                    rendered_bb, anonymous_bb)

    builder.position_at_end(rendered_bb)
    ip_len = builder.call(strlen_fn, [host_buf], name="ip_len")
    ip_len64 = builder.zext(ip_len, i64, name="ip_len64")
    owned = builder.call(malloc_fn, [ip_len64], name="ip_buf")
    memcpy_fn = builder.module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])
    builder.call(memcpy_fn, [owned, host_buf, ip_len64, ir.Constant(ir.IntType(1), 0)])
    builder.store(owned, ip_slot)
    builder.store(ip_len, ip_len_slot)
    builder.branch(build_bb)

    # An unrenderable sender leaves peer_ip empty. The bytes arrived, and they
    # are what the caller asked for; an owned zero-length buffer keeps the
    # field's ownership rule the same on both edges.
    builder.position_at_end(anonymous_bb)
    builder.store(builder.call(malloc_fn, [ir.Constant(i64, 1)], name="empty_ip"), ip_slot)
    builder.store(zero, ip_len_slot)
    builder.branch(build_bb)

    builder.position_at_end(build_bb)
    descriptor = ir.Constant(array_ty, ir.Undefined)
    descriptor = builder.insert_value(descriptor,
                                      builder.trunc(got, i32, name="got32"), 0,
                                      name="desc_len")
    descriptor = builder.insert_value(descriptor, maximum, 1, name="desc_cap")
    descriptor = builder.insert_value(descriptor, buffer, 2, name="descriptor")

    text = cstr_to_fat_pointer_with_len(builder,
                                        builder.load(ip_slot, name="ip_ptr"),
                                        builder.load(ip_len_slot, name="ip_size"),
                                        owned=1)
    peer_port = addr.emit_read_port(builder, storage)

    datagram = ir.Constant(datagram_ty, ir.Undefined)
    datagram = builder.insert_value(datagram, descriptor, 0, name="dg_data")
    datagram = builder.insert_value(datagram, text, 1, name="dg_peer_ip")
    datagram = builder.insert_value(datagram, peer_port, 2, name="datagram")
    builder.ret(emit_ok_result(builder, result_type, datagram, 40))
