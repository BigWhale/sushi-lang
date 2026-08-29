"""The <net/socket> module: BSD sockets as Result-returning primitives.

The byte-level work cannot be written in Sushi -- an array or a struct may not
cross the C ABI (CE5003), there is no way to take a pointer into a u8[], and a
ptr is opaque -- so sockets, sockaddr and getaddrinfo live here, and the typed
wrappers (<net/tcp>, <net/udp>, <net/ip>, <net/dns>) are bundled Sushi source
on top.

The semantic interface is socket_funcs.py; this file only assembles the IR.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src.ir_common import create_stdlib_module
from sushi_lang.sushi_stdlib.src.net.socket_funcs import (  # noqa: F401
    SOCKET_FUNCTIONS,
    get_builtin_socket_function_return_type,
    is_builtin_socket_function,
    validate_socket_function_call,
)


def generate_module_ir() -> ir.Module:
    """Generate the standalone LLVM IR module for <net/socket>."""
    from sushi_lang.sushi_stdlib.src.net import dns, options, stream, tcp

    module = create_stdlib_module("net.socket")
    stream.generate_ir(module)
    tcp.generate_ir(module)
    options.generate_ir(module)
    dns.generate_ir(module)
    return module
