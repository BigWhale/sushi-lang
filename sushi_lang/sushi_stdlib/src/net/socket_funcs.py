"""The <net/socket> semantic interface: names, return types, arity.

No IR lives here. The StdlibRegistry discovers a module by looking for these
three names, built from the module path's last segment, so <net/socket> needs
is_builtin_socket_function, get_builtin_socket_function_return_type and
validate_socket_function_call.

Every function answers Result@(T, NetError). The prefix names the transport:
sock_* works on any descriptor this module produced, tcp_* wants a stream
socket, udp_* a datagram one, and dns_* resolves a name.
"""
from sushi_lang.semantics.typesys import Type, BuiltinType


SOCKET_FUNCTIONS = [
    "sock_close",
]

# Sushi name -> the Ok type of its Result. The Err type is always NetError.
_OK_TYPES = {
    "sock_close": BuiltinType.I32,
}

# Sushi name -> how many arguments it takes.
_ARITY = {
    "sock_close": 1,
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
