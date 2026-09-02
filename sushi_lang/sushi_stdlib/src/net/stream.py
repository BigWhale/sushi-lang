"""Operations on a socket descriptor, whatever transport made it."""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.libc_declarations import (
    declare_free,
    declare_malloc,
    declare_strlen,
)
from sushi_lang.sushi_stdlib.src.error_emission import emit_runtime_error
from sushi_lang.sushi_stdlib.src.net.errno import (
    NET_ERROR_RESOLVE_FAILED,
    emit_errno_err_result,
    emit_net_error_tag,
)
from sushi_lang.sushi_stdlib.src.results import emit_err_result, emit_ok_result
from sushi_lang.sushi_stdlib.src.string_helpers import cstr_to_fat_pointer_with_len
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_byte_array_type,
    get_result_type,
    get_string_type,
    get_unit_enum_type,
)


def generate_ir(module: ir.Module) -> None:
    """Emit every descriptor-level symbol into the module."""
    generate_close(module)
    generate_dup(module)
    generate_local_port(module)
    generate_send(module)
    generate_recv(module)
    generate_peer_ip(module)
    generate_peer_port(module)


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


def generate_dup(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_dup(i32 fd)`.

    A SECOND descriptor over the SAME open socket: the shared-listener primitive, where
    several workers accept on one port. It is the socket twin of `fd_dup`, and it lives
    here for the reason `sock_close` does -- <net/tcp> reaches the kernel through this
    module and answers NetError, not FileError.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    dup_fn = platform_net.declare_dup(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_dup")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    copy = builder.call(dup_fn, [func.args[0]], name="dup_fd")
    ok = builder.icmp_signed(">=", copy, ir.Constant(i32, 0), name="dup_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type, copy, 4))


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


def _emit_getpeername(builder: ir.IRBuilder, module: ir.Module, func: ir.Function,
                      fd: ir.Value):
    """Ask for the peer's address. Answers (ok_flag, storage, len_slot)."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getpeername_fn = platform_net.declare_getpeername(module)

    storage = addr.alloca_zeroed(builder, platform_net.SOCKADDR_STORAGE_SIZE, "ss")
    len_slot = builder.alloca(i32, name="ss_len")
    builder.store(ir.Constant(i32, platform_net.SOCKADDR_STORAGE_SIZE), len_slot)

    rc = builder.call(getpeername_fn, [fd, storage, len_slot], name="getpeername_rc")
    ok = builder.icmp_signed("==", rc, ir.Constant(i32, 0), name="getpeername_ok")
    return ok, storage, len_slot


def generate_peer_port(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_peer_port(i32 fd)`.

    The peer's port is split from its address so that a test can assert the
    address -- which is fixed -- without asserting an ephemeral port.
    """
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_peer_port")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    ok, storage, _len_slot = _emit_getpeername(builder, module, func, func.args[0])
    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    builder.ret(emit_ok_result(builder, result_type,
                               addr.emit_read_port(builder, storage), 4))


def generate_peer_ip(module: ir.Module) -> None:
    """Emit `Result<{i8*,i32,i8}, NetError> sushi_net_sock_peer_ip(i32 fd)`.

    getnameinfo with NI_NUMERICHOST renders the address and never asks a
    resolver, so this makes no network request and works for either family.

    The text is copied into a fresh owned buffer rather than handed out from
    the stack one: owned=1 puts it under Sushi RAII, which is what frees it.
    """
    _i8, i8_ptr, i32, i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getnameinfo_fn = platform_net.declare_getnameinfo(module)
    malloc_fn = declare_malloc(module)
    strlen_fn = declare_strlen(module)

    result_type = get_result_type(get_string_type(), get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_peer_ip")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    host_buf = addr.alloca_zeroed(builder, platform_net.NI_MAXHOST, "host_buf")
    ok, storage, len_slot = _emit_getpeername(builder, module, func, func.args[0])

    named_bb = func.append_basic_block(name="have_peer")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, named_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(named_bb)
    salen = builder.load(len_slot, name="ss_len_value")
    rc = builder.call(getnameinfo_fn, [
        storage, salen,
        host_buf, ir.Constant(i32, platform_net.NI_MAXHOST),
        ir.Constant(i8_ptr, None), ir.Constant(i32, 0),
        ir.Constant(i32, platform_net.NI_NUMERICHOST),
    ], name="getnameinfo_rc")

    # getnameinfo answers an EAI_* code, not errno, and the codes do not share
    # a sign between the platforms. NI_NUMERICHOST needs no resolver, so any
    # failure here is ResolveFailed rather than a system error worth reading.
    rendered_bb = func.append_basic_block(name="rendered")
    gai_fail_bb = func.append_basic_block(name="render_failed")
    builder.cbranch(builder.icmp_signed("==", rc, ir.Constant(i32, 0), name="render_ok"),
                    rendered_bb, gai_fail_bb)

    builder.position_at_end(gai_fail_bb)
    builder.ret(emit_err_result(builder, result_type,
                                ir.Constant(i32, NET_ERROR_RESOLVE_FAILED)))

    builder.position_at_end(rendered_bb)
    length = builder.call(strlen_fn, [host_buf], name="ip_len")
    length64 = builder.zext(length, i64, name="ip_len64")
    owned = builder.call(malloc_fn, [length64], name="ip_buf")
    memcpy_fn = builder.module.declare_intrinsic('llvm.memcpy', [i8_ptr, i8_ptr, i64])
    builder.call(memcpy_fn, [owned, host_buf, length64, ir.Constant(ir.IntType(1), 0)])
    text = cstr_to_fat_pointer_with_len(builder, owned, length, owned=1)
    builder.ret(emit_ok_result(builder, result_type, text, 16))
