"""The <net/socket> semantic interface: one table, and the three discovery names.

No IR lives here. The StdlibRegistry discovers a module by looking for these
three names, built from the module path's last segment, so <net/socket> needs
is_builtin_socket_function, get_builtin_socket_function_return_type and
validate_socket_function_call.

Every function answers Result@(T, NetError). The prefix names the transport:
sock_* works on any descriptor this module produced, tcp_* wants a stream
socket, udp_* a datagram one, and dns_* resolves a name.

SOCKET_SIGNATURES is the ONE spelling of what each primitive takes and answers
(#550). The registry's parameter specs, the arity check, the return type, the
Result the instantiate pass interns and the back end's emission all read it, so
a new primitive is one row here and nothing else. `tests/unit/
test_stdlib_signature_tables.py` is the gate.
"""
from typing import Dict, List

from sushi_lang.semantics.typesys import (
    BuiltinType,
    DynamicArrayType,
    Type,
    UnknownType,
)
from sushi_lang.sushi_stdlib.src.signatures import (
    Signature,
    cstr,
    params_of,
    validate_arity,
)

I32 = BuiltinType.I32
BYTES = DynamicArrayType(BuiltinType.U8)
STRINGS = DynamicArrayType(BuiltinType.STRING)
NET = "NetError"


SOCKET_SIGNATURES: Dict[str, Signature] = {
    # A host name and a port: the host is a C string at the boundary.
    "sock_tcp_connect":      Signature(params_of(cstr(), I32), ok=I32, error=NET),
    "sock_tcp_listen":       Signature(params_of(cstr(), I32, I32), ok=I32, error=NET),
    "sock_udp_bind":         Signature(params_of(cstr(), I32), ok=I32, error=NET),
    "sock_dns_resolve":      Signature(params_of(cstr()), ok=STRINGS, error=NET),
    # One descriptor in, a descriptor or a number out.
    "sock_tcp_accept":       Signature(params_of(I32), ok=I32, error=NET),
    "sock_close":            Signature(params_of(I32), ok=I32, error=NET),
    "sock_dup":              Signature(params_of(I32), ok=I32, error=NET),
    "sock_local_port":       Signature(params_of(I32), ok=I32, error=NET),
    "sock_peer_port":        Signature(params_of(I32), ok=I32, error=NET),
    "sock_peer_ip":          Signature(params_of(I32), ok=BuiltinType.STRING, error=NET),
    # A descriptor and a count, or a descriptor and bytes.
    "sock_recv":             Signature(params_of(I32, I32), ok=BYTES, error=NET),
    "sock_send":             Signature(params_of(I32, BYTES), ok=I32, error=NET),
    "sock_set_recv_timeout": Signature(params_of(I32, I32), ok=I32, error=NET),
    "sock_set_send_timeout": Signature(params_of(I32, I32), ok=I32, error=NET),
    # The datagram pair: send names its peer, receive answers who sent it.
    "sock_udp_send_to":      Signature(params_of(I32, BYTES, cstr(), I32),
                                       ok=I32, error=NET),
    "sock_udp_recv_from":    Signature(params_of(I32, I32),
                                       ok=UnknownType("Datagram"), error=NET),
}

SOCKET_FUNCTIONS: List[str] = list(SOCKET_SIGNATURES)


def is_builtin_socket_function(name: str) -> bool:
    """Whether a bare name is one of the <net/socket> primitives."""
    return name in SOCKET_SIGNATURES


def get_builtin_socket_function_return_type(func_name: str) -> Type:
    """The declared return type: Result@(T, NetError) for every primitive."""
    sig = SOCKET_SIGNATURES.get(func_name)
    if sig is None:
        raise ValueError(f"Unknown socket function: {func_name}")
    return sig.return_type()


def validate_socket_function_call(func_name: str, args: list, reporter, loc) -> None:
    """Check the argument count against the row's own length."""
    validate_arity(func_name, SOCKET_SIGNATURES, args, reporter, loc)
