"""Socket constants for Linux (glibc).

Every number was read from an offsetof probe run in a linux/amd64 container
(2026-08-30) and is asserted in tests/unit/test_net_platform_constants.py.
"""
from sushi_lang.sushi_stdlib.src._platform.posix.net import (  # noqa: F401
    AF_UNSPEC, AF_INET, SOCK_STREAM, SOCK_DGRAM, IPPROTO_TCP, IPPROTO_UDP,
    SHUT_RDWR, AI_PASSIVE, AI_NUMERICHOST, NI_MAXHOST, INET6_ADDRSTRLEN,
    SOCKADDR_IN_SIZE, SOCKADDR_IN6_SIZE, SOCKADDR_STORAGE_SIZE,
    SOCKADDR_PORT_OFFSET, ADDRINFO_SIZE, AI_FLAGS_OFFSET, AI_FAMILY_OFFSET,
    AI_SOCKTYPE_OFFSET, AI_PROTOCOL_OFFSET, AI_ADDRLEN_OFFSET, AI_NEXT_OFFSET,
    TIMEVAL_SIZE, TIMEVAL_USEC_OFFSET,
    declare_socket, declare_connect, declare_bind, declare_listen,
    declare_accept, declare_send, declare_recv, declare_sendto,
    declare_recvfrom, declare_setsockopt, declare_getsockname,
    declare_getpeername, declare_shutdown, declare_getaddrinfo,
    declare_freeaddrinfo, declare_getnameinfo, declare_close, declare_dup,
)

AF_INET6 = 10

SOL_SOCKET = 1
SO_REUSEADDR = 2
SO_RCVTIMEO = 20
SO_SNDTIMEO = 21
SO_ERROR = 4

# There is no SO_NOSIGPIPE here. SIGPIPE is suppressed per CALL, with
# MSG_NOSIGNAL on every send, which is what SEND_FLAGS carries.
SO_NOSIGPIPE = None
SEND_FLAGS = 0x4000

NI_NUMERICHOST = 1

# THE trap, the other way round: darwin has ai_addr at 32 and ai_canonname
# at 24.
AI_ADDR_OFFSET = 24
AI_CANONNAME_OFFSET = 32

# struct timeval.tv_usec is a full __suseconds_t here, an i64. On darwin it is
# an int32 followed by padding.
TIMEVAL_USEC_BITS = 64

# EAI_* codes are negative here and positive on darwin, so only EAI_SYSTEM is
# ever compared and no table of them exists.
EAI_SYSTEM = -11

# The sockaddr family field, for the next person: linux has a u16 family at
# offset 0, where darwin has a u8 sin_len then a u8 family. Nothing in
# <net/socket> reads it -- the port is the only field any generator touches.
SOCKADDR_FAMILY_OFFSET = 0
SOCKADDR_FAMILY_BITS = 16
SOCKADDR_HAS_LEN = False
