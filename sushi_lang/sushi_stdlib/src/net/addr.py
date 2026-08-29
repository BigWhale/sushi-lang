"""The sockaddr and addrinfo vocabulary shared by the <net/socket> generators.

Nothing here ever reads an address family. The only field any generator
touches is the port, and sin_port and sin6_port both sit at
SOCKADDR_PORT_OFFSET, so a v4 and a v6 address are handled by one code path.
That is also why the port is patched into whatever getaddrinfo returned rather
than passed to it as a service string.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src._platform import get_platform_module
from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types


def i8_array(size: int) -> ir.ArrayType:
    """The type of an opaque byte buffer of `size` bytes."""
    return ir.ArrayType(ir.IntType(8), size)


def alloca_zeroed(builder: ir.IRBuilder, size: int, name: str) -> ir.Value:
    """An entry-block byte buffer of `size` zero bytes, as an i8*.

    Every alloca in this module is hoisted to the entry block: one inside a
    loop grows the frame per iteration, and the addrinfo walk can be long.
    """
    _i8, i8_ptr, _i32, _i64 = get_basic_types()
    buf_ty = i8_array(size)
    slot = builder.alloca(buf_ty, name=name)
    builder.store(ir.Constant(buf_ty, None), slot)
    return builder.bitcast(slot, i8_ptr, name=f"{name}_ptr")


def _at(builder: ir.IRBuilder, base: ir.Value, offset: int, pointee: ir.Type,
        name: str) -> ir.Value:
    """A typed pointer to `offset` bytes into an i8* buffer."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    byte_ptr = builder.gep(base, [ir.Constant(i32, offset)], name=f"{name}_raw")
    return builder.bitcast(byte_ptr, pointee.as_pointer(), name=name)


def store_i32_at(builder: ir.IRBuilder, base: ir.Value, offset: int,
                 value: ir.Value, name: str) -> None:
    """Write one i32 field into a byte buffer."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    builder.store(value, _at(builder, base, offset, i32, name))


def load_i32_at(builder: ir.IRBuilder, base: ir.Value, offset: int,
                name: str) -> ir.Value:
    """Read one i32 field out of a byte buffer."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    return builder.load(_at(builder, base, offset, i32, f"{name}_ptr"), name=name)


def load_ptr_at(builder: ir.IRBuilder, base: ir.Value, offset: int,
                name: str) -> ir.Value:
    """Read one i8* field out of a byte buffer."""
    _i8, i8_ptr, _i32, _i64 = get_basic_types()
    return builder.load(_at(builder, base, offset, i8_ptr, f"{name}_ptr"), name=name)


def _bswap16(builder: ir.IRBuilder, value: ir.Value, name: str) -> ir.Value:
    """Swap the two bytes of an i16.

    Plain shifts rather than htons/ntohs: those are inline macros on macOS and
    are not reliably real symbols to link against.
    """
    i16 = ir.IntType(16)
    high = builder.shl(value, ir.Constant(i16, 8), name=f"{name}_hi")
    low = builder.lshr(value, ir.Constant(i16, 8), name=f"{name}_lo")
    return builder.or_(high, low, name=name)


def emit_patch_port(builder: ir.IRBuilder, sockaddr: ir.Value,
                    port: ir.Value) -> None:
    """Write a host-order i32 port into a sockaddr, in network order.

    getaddrinfo is asked for the host alone, so the address it answers with
    carries port 0; this is what puts the caller's port in.
    """
    platform_net = get_platform_module('net')
    i16 = ir.IntType(16)
    port16 = builder.trunc(port, i16, name="port16")
    slot = _at(builder, sockaddr, platform_net.SOCKADDR_PORT_OFFSET, i16, "sin_port")
    builder.store(_bswap16(builder, port16, "port_net"), slot)


def emit_read_port(builder: ir.IRBuilder, sockaddr: ir.Value) -> ir.Value:
    """Read the port out of a sockaddr as a host-order i32."""
    platform_net = get_platform_module('net')
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    i16 = ir.IntType(16)
    slot = _at(builder, sockaddr, platform_net.SOCKADDR_PORT_OFFSET, i16, "sin_port")
    net_order = builder.load(slot, name="port_net")
    return builder.zext(_bswap16(builder, net_order, "port_host"), i32, name="port")


def emit_hints(builder: ir.IRBuilder, socktype: int, passive: bool) -> ir.Value:
    """An addrinfo of hints: AF_UNSPEC, the given socket type, zero elsewhere.

    AF_UNSPEC lets the resolver answer with either family, and the caller walks
    the list; nothing downstream has to know which one it got.
    """
    platform_net = get_platform_module('net')
    _i8, _i8_ptr, i32, _i64 = get_basic_types()

    hints = alloca_zeroed(builder, platform_net.ADDRINFO_SIZE, "hints")
    if passive:
        store_i32_at(builder, hints, platform_net.AI_FLAGS_OFFSET,
                     ir.Constant(i32, platform_net.AI_PASSIVE), "ai_flags")
    store_i32_at(builder, hints, platform_net.AI_FAMILY_OFFSET,
                 ir.Constant(i32, platform_net.AF_UNSPEC), "ai_family")
    store_i32_at(builder, hints, platform_net.AI_SOCKTYPE_OFFSET,
                 ir.Constant(i32, socktype), "ai_socktype")
    return hints


def emit_node_or_null(builder: ir.IRBuilder, host: ir.Value) -> ir.Value:
    """The host to resolve, or NULL when the caller passed an empty string.

    NULL with AI_PASSIVE is the wildcard address, which is what an empty host
    means to a listener. A select rather than a branch: the string is always a
    valid pointer, so reading its first byte is safe.
    """
    _i8, i8_ptr, _i32, _i64 = get_basic_types()
    i8 = ir.IntType(8)
    first = builder.load(host, name="host_first_byte")
    is_empty = builder.icmp_signed("==", first, ir.Constant(i8, 0), name="host_empty")
    return builder.select(is_empty, ir.Constant(i8_ptr, None), host, name="node")


def alloca_one_i32(builder: ir.IRBuilder, name: str) -> ir.Value:
    """An entry-block i32 holding 1, for the setsockopt calls that want a flag."""
    _i8, _i8_ptr, i32, _i64 = get_basic_types()
    slot = builder.alloca(i32, name=name)
    builder.store(ir.Constant(i32, 1), slot)
    return slot


def emit_nosigpipe(builder: ir.IRBuilder, module: ir.Module, fd: ir.Value,
                   one_slot: ir.Value) -> None:
    """Stop a write to a dead peer from killing the process.

    macOS suppresses SIGPIPE per SOCKET, once, here. Linux has no
    SO_NOSIGPIPE and does it per CALL instead, with MSG_NOSIGNAL in SEND_FLAGS.
    An accepted descriptor does NOT inherit the option, so every socket this
    module hands out passes through here.

    `one_slot` comes from the caller's ENTRY block: this runs inside the
    getaddrinfo walk, and an alloca in a loop grows the frame per iteration.
    """
    platform_net = get_platform_module('net')
    if platform_net.SO_NOSIGPIPE is None:
        return
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    setsockopt_fn = platform_net.declare_setsockopt(module)
    builder.call(setsockopt_fn, [
        fd,
        ir.Constant(i32, platform_net.SOL_SOCKET),
        ir.Constant(i32, platform_net.SO_NOSIGPIPE),
        builder.bitcast(one_slot, i8_ptr),
        ir.Constant(i32, 4),
    ])
