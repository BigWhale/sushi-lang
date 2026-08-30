"""The <net/socket> semantic interface: names, return types, arity.

No IR lives here. The StdlibRegistry discovers a module by looking for these
three names, built from the module path's last segment, so <net/socket> needs
is_builtin_socket_function, get_builtin_socket_function_return_type and
validate_socket_function_call.

Every function answers Result@(T, NetError). The prefix names the transport:
sock_* works on any descriptor this module produced, tcp_* wants a stream
socket, udp_* a datagram one, and dns_* resolves a name.
"""
from typing import Dict, List

from sushi_lang.semantics.typesys import (
    BuiltinType,
    DynamicArrayType,
    Type,
    UnknownType,
)


SOCKET_FUNCTIONS: List[str] = [
    "sock_tcp_connect",
    "sock_tcp_listen",
    "sock_tcp_accept",
    "sock_send",
    "sock_recv",
    "sock_close",
    "sock_local_port",
    "sock_peer_ip",
    "sock_peer_port",
    "sock_set_recv_timeout",
    "sock_set_send_timeout",
    "sock_dns_resolve",
    "sock_udp_bind",
    "sock_udp_send_to",
    "sock_udp_recv_from",
]

# Sushi name -> the Ok type of its Result. The Err type is always NetError.
_OK_TYPES: Dict[str, Type] = {
    "sock_tcp_connect": BuiltinType.I32,
    "sock_tcp_listen": BuiltinType.I32,
    "sock_tcp_accept": BuiltinType.I32,
    "sock_send": BuiltinType.I32,
    "sock_recv": DynamicArrayType(BuiltinType.U8),
    "sock_close": BuiltinType.I32,
    "sock_local_port": BuiltinType.I32,
    "sock_peer_ip": BuiltinType.STRING,
    "sock_peer_port": BuiltinType.I32,
    "sock_set_recv_timeout": BuiltinType.I32,
    "sock_set_send_timeout": BuiltinType.I32,
    "sock_dns_resolve": DynamicArrayType(BuiltinType.STRING),
    "sock_udp_bind": BuiltinType.I32,
    "sock_udp_send_to": BuiltinType.I32,
    "sock_udp_recv_from": UnknownType("Datagram"),
}

# Sushi name -> how many arguments it takes.
_ARITY: Dict[str, int] = {
    "sock_tcp_connect": 2,
    "sock_tcp_listen": 3,
    "sock_tcp_accept": 1,
    "sock_send": 2,
    "sock_recv": 2,
    "sock_close": 1,
    "sock_local_port": 1,
    "sock_peer_ip": 1,
    "sock_peer_port": 1,
    "sock_set_recv_timeout": 2,
    "sock_set_send_timeout": 2,
    "sock_dns_resolve": 1,
    "sock_udp_bind": 2,
    "sock_udp_send_to": 4,
    "sock_udp_recv_from": 2,
}


def is_builtin_socket_function(name: str) -> bool:
    """Whether a bare name is one of the <net/socket> primitives."""
    return name in SOCKET_FUNCTIONS


def get_builtin_socket_function_return_type(func_name: str) -> Type:
    """The declared return type: Result@(T, NetError) for every primitive."""
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import UnknownType

    ok_type = _OK_TYPES.get(func_name)
    if ok_type is None:
        raise ValueError(f"Unknown socket function: {func_name}")
    return GenericTypeRef("Result", (ok_type, UnknownType("NetError")))


def validate_socket_function_call(func_name: str, args: list, reporter, loc) -> None:
    """Check the argument count against the declared arity."""
    from sushi_lang.internals import errors as er

    expected = _ARITY.get(func_name)
    if expected is not None and len(args) != expected:
        er.emit(reporter, er.ERR.CE2009, loc,
                name=func_name, expected=expected, got=len(args))
