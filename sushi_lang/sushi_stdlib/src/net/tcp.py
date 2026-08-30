"""TCP: connect by name, listen, accept.

connect and listen share one skeleton -- resolve the host, walk the answers,
try each until one works -- so it lives in _emit_addrinfo_walk and the two
callers supply the block that uses a candidate address.

Every loop variable is an entry-block alloca and there is not one phi. That is
the house style here (read_dir.py and copy.py have none either) and mem2reg
promotes them all.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.net import addr
from sushi_lang.sushi_stdlib.src.net.errno import (
    NET_ERROR_RESOLVE_FAILED,
    emit_errno_err_result,
    emit_net_error_tag,
)
from sushi_lang.sushi_stdlib.src.results import emit_err_result, emit_ok_result
from sushi_lang.sushi_stdlib.src.type_definitions import (
    get_basic_types,
    get_result_type,
    get_unit_enum_type,
)
from sushi_lang.backend.runtime.constants import ERRNO_DEFAULT_NET_ERROR


def generate_ir(module: ir.Module) -> None:
    """Emit every TCP symbol into the module."""
    generate_connect(module)
    generate_listen(module)
    generate_accept(module)


def generate_listen(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_tcp_listen(i8*, i32, i32)`.

    SO_REUSEADDR is set unconditionally, before bind: it is what every server
    wants, and it keeps a bool out of the ABI. Port 0 asks the kernel to choose,
    which sock_local_port then reads back.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr, i32, i32]),
                       name="sushi_net_sock_tcp_listen")
    host, port, backlog = func.args
    host.name, port.name, backlog.name = "host", "port", "backlog"

    builder = ir.IRBuilder(func.append_basic_block(name="entry"))
    one_slot = addr.alloca_one_i32(builder, "opt_on")
    setsockopt_fn = platform_net.declare_setsockopt(module)
    bind_fn = platform_net.declare_bind(module)
    listen_fn = platform_net.declare_listen(module)

    def use_candidate(b, fd, sockaddr, salen, on_success, on_failure):
        """Set SO_REUSEADDR, bind, listen. Branch to on_success once listening."""
        b.call(setsockopt_fn, [
            fd,
            ir.Constant(i32, platform_net.SOL_SOCKET),
            ir.Constant(i32, platform_net.SO_REUSEADDR),
            b.bitcast(one_slot, i8_ptr),
            ir.Constant(i32, 4),
        ])
        addr.emit_patch_port(b, sockaddr, port)

        bound_bb = func.append_basic_block(name="bound")
        rc = b.call(bind_fn, [fd, sockaddr, salen], name="bind_rc")
        b.cbranch(b.icmp_signed("==", rc, ir.Constant(i32, 0), name="bind_ok"),
                  bound_bb, on_failure)

        b.position_at_end(bound_bb)
        rc = b.call(listen_fn, [fd, backlog], name="listen_rc")
        b.cbranch(b.icmp_signed("==", rc, ir.Constant(i32, 0), name="listen_ok"),
                  on_success, on_failure)

    _emit_addrinfo_walk(builder, func, module, result_type, host,
                        platform_net.SOCK_STREAM, passive=True,
                        one_slot=one_slot, use_candidate=use_candidate)


def _emit_addrinfo_walk(builder, func, module, result_type, host, socktype,
                        passive, one_slot, use_candidate):
    """Resolve `host`, then try each answer until `use_candidate` succeeds.

    The failure bookkeeping is the delicate part. close() overwrites errno, so
    each failing edge reads the NetError tag FIRST and parks it in err_slot;
    the exhausted block then reports the LAST failure rather than a tag read
    after the cleanup that destroyed it.

    freeaddrinfo runs on both exits that had a successful getaddrinfo, and on
    neither failure exit: POSIX leaves the result unspecified when the call
    fails, so touching it there is the bug.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    getaddrinfo_fn = platform_net.declare_getaddrinfo(module)
    freeaddrinfo_fn = platform_net.declare_freeaddrinfo(module)
    socket_fn = platform_net.declare_socket(module)
    close_fn = platform_net.declare_close(module)

    zero = ir.Constant(i32, 0)
    null = ir.Constant(i8_ptr, None)

    res_slot = builder.alloca(i8_ptr, name="res_slot")
    cur_slot = builder.alloca(i8_ptr, name="cur_slot")
    err_slot = builder.alloca(i32, name="err_slot")
    fd_slot = builder.alloca(i32, name="fd_slot")
    builder.store(ir.Constant(i32, ERRNO_DEFAULT_NET_ERROR), err_slot)

    hints = addr.emit_hints(builder, socktype, passive)
    node = addr.emit_node_or_null(builder, host)
    rc = builder.call(getaddrinfo_fn, [node, null, hints, res_slot], name="gai_rc")

    gai_ok_bb = func.append_basic_block(name="gai_ok")
    gai_fail_bb = func.append_basic_block(name="gai_fail")
    builder.cbranch(builder.icmp_signed("==", rc, zero, name="gai_ok_p"),
                    gai_ok_bb, gai_fail_bb)

    # A getaddrinfo answer is an EAI_* code, not errno, and the codes do not
    # even share a sign between the platforms. Only EAI_SYSTEM is comparable:
    # it means the real error is in errno. Everything else is ResolveFailed.
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

    loop_bb = func.append_basic_block(name="loop_head")
    builder.position_at_end(gai_ok_bb)
    builder.store(builder.load(res_slot, name="res"), cur_slot)
    builder.branch(loop_bb)

    try_bb = func.append_basic_block(name="try_one")
    exhausted_bb = func.append_basic_block(name="exhausted")
    builder.position_at_end(loop_bb)
    cur = builder.load(cur_slot, name="cur")
    builder.cbranch(builder.icmp_unsigned("==", cur, null, name="at_end"),
                    exhausted_bb, try_bb)

    advance_bb = func.append_basic_block(name="advance")
    park_bb = func.append_basic_block(name="park_and_advance")
    close_and_advance_bb = func.append_basic_block(name="close_and_advance")
    success_bb = func.append_basic_block(name="succeeded")

    builder.position_at_end(try_bb)
    cur = builder.load(cur_slot, name="cur_try")
    family = addr.load_i32_at(builder, cur, platform_net.AI_FAMILY_OFFSET, "ai_family")
    stype = addr.load_i32_at(builder, cur, platform_net.AI_SOCKTYPE_OFFSET, "ai_socktype")
    proto = addr.load_i32_at(builder, cur, platform_net.AI_PROTOCOL_OFFSET, "ai_protocol")
    fd = builder.call(socket_fn, [family, stype, proto], name="fd")
    have_bb = func.append_basic_block(name="have_socket")
    builder.cbranch(builder.icmp_signed("<", fd, zero, name="socket_failed"),
                    park_bb, have_bb)

    builder.position_at_end(have_bb)
    addr.emit_nosigpipe(builder, module, fd, one_slot)
    sockaddr = addr.load_ptr_at(builder, cur, platform_net.AI_ADDR_OFFSET, "ai_addr")
    salen = addr.load_i32_at(builder, cur, platform_net.AI_ADDRLEN_OFFSET, "ai_addrlen")
    builder.store(fd, fd_slot)
    use_candidate(builder, fd, sockaddr, salen, success_bb, close_and_advance_bb)

    # errno first, then the close that would overwrite it.
    builder.position_at_end(close_and_advance_bb)
    builder.store(emit_net_error_tag(builder, module), err_slot)
    builder.call(close_fn, [builder.load(fd_slot, name="fd_to_close")])
    builder.branch(advance_bb)

    builder.position_at_end(park_bb)
    builder.store(emit_net_error_tag(builder, module), err_slot)
    builder.branch(advance_bb)

    builder.position_at_end(advance_bb)
    cur = builder.load(cur_slot, name="cur_advance")
    nxt = addr.load_ptr_at(builder, cur, platform_net.AI_NEXT_OFFSET, "ai_next")
    builder.store(nxt, cur_slot)
    builder.branch(loop_bb)

    builder.position_at_end(success_bb)
    builder.call(freeaddrinfo_fn, [builder.load(res_slot, name="res_free_ok")])
    builder.ret(emit_ok_result(builder, result_type,
                               builder.load(fd_slot, name="fd_ok"), 4))

    builder.position_at_end(exhausted_bb)
    builder.call(freeaddrinfo_fn, [builder.load(res_slot, name="res_free_end")])
    builder.ret(emit_err_result(builder, result_type,
                                builder.load(err_slot, name="last_error")))


def generate_connect(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_tcp_connect(i8*, i32)`.

    getaddrinfo is asked for the host alone and the port is patched into each
    answer, because sin_port and sin6_port share an offset and a service string
    would mean rendering the port back into text.

    There is no connect timeout: that needs a non-blocking socket and select,
    which is a later item. A black-holed address blocks for as long as the
    kernel takes to give up.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i8_ptr, i32]),
                       name="sushi_net_sock_tcp_connect")
    host, port = func.args
    host.name, port.name = "host", "port"

    builder = ir.IRBuilder(func.append_basic_block(name="entry"))
    one_slot = addr.alloca_one_i32(builder, "opt_on")
    connect_fn = platform_net.declare_connect(module)

    def use_candidate(b, fd, sockaddr, salen, on_success, on_failure):
        """Patch the port in and connect."""
        addr.emit_patch_port(b, sockaddr, port)
        rc = b.call(connect_fn, [fd, sockaddr, salen], name="connect_rc")
        b.cbranch(b.icmp_signed("==", rc, ir.Constant(i32, 0), name="connect_ok"),
                  on_success, on_failure)

    _emit_addrinfo_walk(builder, func, module, result_type, host,
                        platform_net.SOCK_STREAM, passive=False,
                        one_slot=one_slot, use_candidate=use_candidate)


def generate_accept(module: ir.Module) -> None:
    """Emit `Result<i32, NetError> sushi_net_sock_tcp_accept(i32 fd)`.

    The peer is not returned: Sushi has no tuples, and sock_peer_ip works on
    the accepted descriptor afterwards. The accepted descriptor does NOT
    inherit SO_NOSIGPIPE, so it is set here too.

    A listening socket carrying SO_RCVTIMEO makes accept() answer EAGAIN when
    it expires, which the table maps to TimedOut. That is what gives a test a
    bound rather than a hang.
    """
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    platform_net = get_platform_module('net')
    accept_fn = platform_net.declare_accept(module)

    result_type = get_result_type(i32, get_unit_enum_type())
    func = ir.Function(module, ir.FunctionType(result_type, [i32]),
                       name="sushi_net_sock_tcp_accept")
    func.args[0].name = "fd"
    builder = ir.IRBuilder(func.append_basic_block(name="entry"))

    one_slot = addr.alloca_one_i32(builder, "opt_on")
    storage = addr.alloca_zeroed(builder, platform_net.SOCKADDR_STORAGE_SIZE, "ss")
    len_slot = builder.alloca(i32, name="ss_len")
    builder.store(ir.Constant(i32, platform_net.SOCKADDR_STORAGE_SIZE), len_slot)

    fd = builder.call(accept_fn, [func.args[0], storage, len_slot], name="accepted_fd")
    ok = builder.icmp_signed(">=", fd, ir.Constant(i32, 0), name="accept_ok")

    success_bb = func.append_basic_block(name="success")
    failure_bb = func.append_basic_block(name="failure")
    builder.cbranch(ok, success_bb, failure_bb)

    builder.position_at_end(failure_bb)
    builder.ret(emit_errno_err_result(builder, module, result_type))

    builder.position_at_end(success_bb)
    addr.emit_nosigpipe(builder, module, fd, one_slot)
    builder.ret(emit_ok_result(builder, result_type, fd, 4))
