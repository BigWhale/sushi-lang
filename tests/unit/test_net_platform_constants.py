"""The <net/socket> platform constants, pinned for both platforms.

Every number here came from an offsetof probe run on the platform (2026-08-30):
natively on macOS arm64, and in a linux/amd64 container for glibc. A typo in a
socket offset does not fail loudly -- it reads one pointer as another and the
call gets garbage -- so the tables are asserted here rather than trusted.

Both platform modules are imported regardless of the host, so a change to
either one is caught on either machine.
"""
from __future__ import annotations

import pytest

from sushi_lang.sushi_stdlib.src._platform.darwin import net as darwin_net
from sushi_lang.sushi_stdlib.src._platform.linux import net as linux_net


DARWIN = {
    "AF_INET6": 30,
    "SOL_SOCKET": 0xFFFF,
    "SO_REUSEADDR": 0x0004,
    "SO_RCVTIMEO": 0x1006,
    "SO_SNDTIMEO": 0x1005,
    "SO_NOSIGPIPE": 0x1022,
    "SEND_FLAGS": 0,
    "NI_NUMERICHOST": 2,
    "AI_ADDR_OFFSET": 32,
    "AI_CANONNAME_OFFSET": 24,
    "TIMEVAL_USEC_BITS": 32,
    "EAI_SYSTEM": 11,
}

LINUX = {
    "AF_INET6": 10,
    "SOL_SOCKET": 1,
    "SO_REUSEADDR": 2,
    "SO_RCVTIMEO": 20,
    "SO_SNDTIMEO": 21,
    "SO_NOSIGPIPE": None,
    "SEND_FLAGS": 0x4000,
    "NI_NUMERICHOST": 1,
    "AI_ADDR_OFFSET": 24,
    "AI_CANONNAME_OFFSET": 32,
    "TIMEVAL_USEC_BITS": 64,
    "EAI_SYSTEM": -11,
}

# The constants both platforms agree on. They live in posix/net.py and each
# platform module re-exports them, so reading them off either module is a test
# that the re-export happened.
SHARED = {
    "AF_UNSPEC": 0,
    "AF_INET": 2,
    "SOCK_STREAM": 1,
    "SOCK_DGRAM": 2,
    "SHUT_RDWR": 2,
    "AI_PASSIVE": 1,
    "AI_NUMERICHOST": 4,
    "NI_MAXHOST": 1025,
    "SOCKADDR_IN_SIZE": 16,
    "SOCKADDR_IN6_SIZE": 28,
    "SOCKADDR_STORAGE_SIZE": 128,
    "SOCKADDR_PORT_OFFSET": 2,
    "ADDRINFO_SIZE": 48,
    "AI_FAMILY_OFFSET": 4,
    "AI_SOCKTYPE_OFFSET": 8,
    "AI_PROTOCOL_OFFSET": 12,
    "AI_ADDRLEN_OFFSET": 16,
    "AI_NEXT_OFFSET": 40,
    "TIMEVAL_SIZE": 16,
    "TIMEVAL_USEC_OFFSET": 8,
}


@pytest.mark.parametrize("name,expected", sorted(DARWIN.items()))
def test_darwin_constant(name, expected):
    assert getattr(darwin_net, name) == expected


@pytest.mark.parametrize("name,expected", sorted(LINUX.items()))
def test_linux_constant(name, expected):
    assert getattr(linux_net, name) == expected


@pytest.mark.parametrize("module", [darwin_net, linux_net], ids=["darwin", "linux"])
@pytest.mark.parametrize("name,expected", sorted(SHARED.items()))
def test_shared_constant(module, name, expected):
    assert getattr(module, name) == expected


def test_the_addrinfo_pointers_are_swapped():
    """The one trap: ai_addr and ai_canonname trade places between the platforms.

    Reading a char* as a sockaddr* is not a crash, it is a connect() to
    nowhere, so the swap gets its own assertion rather than riding along in
    the table above.
    """
    assert darwin_net.AI_ADDR_OFFSET == linux_net.AI_CANONNAME_OFFSET
    assert darwin_net.AI_CANONNAME_OFFSET == linux_net.AI_ADDR_OFFSET


def test_only_linux_needs_a_send_flag():
    """SIGPIPE is suppressed per-socket on darwin and per-call on linux."""
    assert darwin_net.SO_NOSIGPIPE is not None and darwin_net.SEND_FLAGS == 0
    assert linux_net.SO_NOSIGPIPE is None and linux_net.SEND_FLAGS != 0
