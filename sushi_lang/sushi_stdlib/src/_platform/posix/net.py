"""POSIX socket declarations, and the constants both platforms agree on.

The declarations are the BSD socket API as libc exports it. A sockaddr is an
opaque i8* here: no generator in <net/socket> ever reads its family, because
the only field any of them touches is the port, and sin_port and sin6_port are
both at SOCKADDR_PORT_OFFSET.

Every constant below was read from an offsetof probe on both platforms
(2026-08-30) and is asserted in tests/unit/test_net_platform_constants.py. The
ones that DISAGREE live in _platform/darwin/net.py and _platform/linux/net.py.
"""
from llvmlite import ir

from sushi_lang.sushi_stdlib.src.type_definitions import get_basic_types
from sushi_lang.sushi_stdlib.src._platform.posix.files import declare_close

AF_UNSPEC = 0
AF_INET = 2
SOCK_STREAM = 1
SOCK_DGRAM = 2
IPPROTO_TCP = 6
IPPROTO_UDP = 17
SHUT_RDWR = 2

AI_PASSIVE = 1
AI_NUMERICHOST = 4
NI_MAXHOST = 1025
INET6_ADDRSTRLEN = 46

# sockaddr_in is 16 bytes and sockaddr_in6 is 28 on both platforms, and the
# port sits at offset 2 in BOTH -- which is what lets connect-by-name patch the
# port into whatever getaddrinfo returned without ever branching on family.
SOCKADDR_IN_SIZE = 16
SOCKADDR_IN6_SIZE = 28
SOCKADDR_STORAGE_SIZE = 128
SOCKADDR_PORT_OFFSET = 2

# struct addrinfo. ai_addr and ai_canonname are NOT here: they trade places
# between the platforms, so they live in the per-platform files.
ADDRINFO_SIZE = 48
AI_FLAGS_OFFSET = 0
AI_FAMILY_OFFSET = 4
AI_SOCKTYPE_OFFSET = 8
AI_PROTOCOL_OFFSET = 12
AI_ADDRLEN_OFFSET = 16
AI_NEXT_OFFSET = 40

# struct timeval. tv_usec is at offset 8 on both, but its WIDTH differs, so
# TIMEVAL_USEC_BITS is per-platform.
TIMEVAL_SIZE = 16
TIMEVAL_USEC_OFFSET = 8


def _declare(module: ir.Module, name: str, ret, args) -> ir.Function:
    """Declare one libc symbol once per module."""
    try:
        return module.get_global(name)
    except KeyError:
        return ir.Function(module, ir.FunctionType(ret, args), name=name)


def declare_socket(module: ir.Module) -> ir.Function:
    """int socket(int domain, int type, int protocol)"""
    _i8, _i8p, i32, _i64 = get_basic_types()
    return _declare(module, "socket", i32, [i32, i32, i32])


def declare_connect(module: ir.Module) -> ir.Function:
    """int connect(int fd, const struct sockaddr *addr, socklen_t len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "connect", i32, [i32, i8_ptr, i32])


def declare_bind(module: ir.Module) -> ir.Function:
    """int bind(int fd, const struct sockaddr *addr, socklen_t len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "bind", i32, [i32, i8_ptr, i32])


def declare_listen(module: ir.Module) -> ir.Function:
    """int listen(int fd, int backlog)"""
    _i8, _i8p, i32, _i64 = get_basic_types()
    return _declare(module, "listen", i32, [i32, i32])


def declare_accept(module: ir.Module) -> ir.Function:
    """int accept(int fd, struct sockaddr *addr, socklen_t *len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "accept", i32, [i32, i8_ptr, i32.as_pointer()])


def declare_send(module: ir.Module) -> ir.Function:
    """ssize_t send(int fd, const void *buf, size_t n, int flags)"""
    _i8, i8_ptr, i32, i64 = get_basic_types()
    return _declare(module, "send", i64, [i32, i8_ptr, i64, i32])


def declare_recv(module: ir.Module) -> ir.Function:
    """ssize_t recv(int fd, void *buf, size_t n, int flags)"""
    _i8, i8_ptr, i32, i64 = get_basic_types()
    return _declare(module, "recv", i64, [i32, i8_ptr, i64, i32])


def declare_sendto(module: ir.Module) -> ir.Function:
    """ssize_t sendto(int fd, const void *buf, size_t n, int flags,
                      const struct sockaddr *to, socklen_t tolen)"""
    _i8, i8_ptr, i32, i64 = get_basic_types()
    return _declare(module, "sendto", i64, [i32, i8_ptr, i64, i32, i8_ptr, i32])


def declare_recvfrom(module: ir.Module) -> ir.Function:
    """ssize_t recvfrom(int fd, void *buf, size_t n, int flags,
                        struct sockaddr *from, socklen_t *fromlen)"""
    _i8, i8_ptr, i32, i64 = get_basic_types()
    return _declare(module, "recvfrom", i64,
                    [i32, i8_ptr, i64, i32, i8_ptr, i32.as_pointer()])


def declare_setsockopt(module: ir.Module) -> ir.Function:
    """int setsockopt(int fd, int level, int name, const void *val, socklen_t len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "setsockopt", i32, [i32, i32, i32, i8_ptr, i32])


def declare_getsockname(module: ir.Module) -> ir.Function:
    """int getsockname(int fd, struct sockaddr *addr, socklen_t *len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "getsockname", i32, [i32, i8_ptr, i32.as_pointer()])


def declare_getpeername(module: ir.Module) -> ir.Function:
    """int getpeername(int fd, struct sockaddr *addr, socklen_t *len)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "getpeername", i32, [i32, i8_ptr, i32.as_pointer()])


def declare_shutdown(module: ir.Module) -> ir.Function:
    """int shutdown(int fd, int how)"""
    _i8, _i8p, i32, _i64 = get_basic_types()
    return _declare(module, "shutdown", i32, [i32, i32])


def declare_getaddrinfo(module: ir.Module) -> ir.Function:
    """int getaddrinfo(const char *node, const char *service,
                       const struct addrinfo *hints, struct addrinfo **res)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "getaddrinfo", i32,
                    [i8_ptr, i8_ptr, i8_ptr, i8_ptr.as_pointer()])


def declare_freeaddrinfo(module: ir.Module) -> ir.Function:
    """void freeaddrinfo(struct addrinfo *res)"""
    _i8, i8_ptr, _i32, _i64 = get_basic_types()
    return _declare(module, "freeaddrinfo", ir.VoidType(), [i8_ptr])


def declare_getnameinfo(module: ir.Module) -> ir.Function:
    """int getnameinfo(const struct sockaddr *sa, socklen_t salen,
                       char *host, socklen_t hostlen,
                       char *serv, socklen_t servlen, int flags)"""
    _i8, i8_ptr, i32, _i64 = get_basic_types()
    return _declare(module, "getnameinfo", i32,
                    [i8_ptr, i32, i8_ptr, i32, i8_ptr, i32, i32])


__all__ = [
    "AF_UNSPEC", "AF_INET", "SOCK_STREAM", "SOCK_DGRAM", "IPPROTO_TCP",
    "IPPROTO_UDP", "SHUT_RDWR", "AI_PASSIVE", "AI_NUMERICHOST", "NI_MAXHOST",
    "INET6_ADDRSTRLEN", "SOCKADDR_IN_SIZE", "SOCKADDR_IN6_SIZE",
    "SOCKADDR_STORAGE_SIZE", "SOCKADDR_PORT_OFFSET", "ADDRINFO_SIZE",
    "AI_FLAGS_OFFSET", "AI_FAMILY_OFFSET", "AI_SOCKTYPE_OFFSET",
    "AI_PROTOCOL_OFFSET", "AI_ADDRLEN_OFFSET", "AI_NEXT_OFFSET",
    "TIMEVAL_SIZE", "TIMEVAL_USEC_OFFSET",
    "declare_socket", "declare_connect", "declare_bind", "declare_listen",
    "declare_accept", "declare_send", "declare_recv", "declare_sendto",
    "declare_recvfrom", "declare_setsockopt", "declare_getsockname",
    "declare_getpeername", "declare_shutdown", "declare_getaddrinfo",
    "declare_freeaddrinfo", "declare_getnameinfo", "declare_close",
]
