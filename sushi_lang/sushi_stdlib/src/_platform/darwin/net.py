"""Socket constants for macOS.

Every number was read from an offsetof probe run natively on this platform
(2026-08-30) and is asserted in tests/unit/test_net_platform_constants.py. The
declarations themselves are shared: unlike stat, the darwin socket symbols
carry no $UNIX2003 or $NOCANCEL suffix on either arch, so there is nothing to
override.
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
    declare_freeaddrinfo, declare_getnameinfo, declare_close,
)

AF_INET6 = 30

SOL_SOCKET = 0xFFFF
SO_REUSEADDR = 0x0004
SO_RCVTIMEO = 0x1006
SO_SNDTIMEO = 0x1005
SO_ERROR = 0x1007

# SIGPIPE is suppressed per SOCKET here, once at creation, so send() carries no
# flag. Linux has no SO_NOSIGPIPE and does it per CALL instead.
SO_NOSIGPIPE = 0x1022
SEND_FLAGS = 0

NI_NUMERICHOST = 0x0002

# THE trap. ai_addr and ai_canonname trade places between the platforms: linux
# has ai_addr at 24 and ai_canonname at 32. Reading one as the other is not a
# crash, it is a connect() to a string.
AI_ADDR_OFFSET = 32
AI_CANONNAME_OFFSET = 24

# struct timeval.tv_usec is __darwin_suseconds_t, an int32; bytes 12-15 are
# padding. On linux it is a full i64.
TIMEVAL_USEC_BITS = 32

# getaddrinfo answers with EAI_* codes, which are NOT errno and whose sign
# flips between the platforms (EAI_NONAME is 8 here and -2 on linux). Only
# EAI_SYSTEM is ever compared, which is why no table of them exists.
EAI_SYSTEM = 11

# The sockaddr family field, for the next person: darwin puts a u8 sin_len at
# offset 0 and the family in a u8 at offset 1, where linux has a u16 family at
# offset 0. Nothing in <net/socket> reads it -- the port is the only field any
# generator touches -- but it is the first fact anyone will reach for.
SOCKADDR_FAMILY_OFFSET = 1
SOCKADDR_FAMILY_BITS = 8
SOCKADDR_HAS_LEN = True
