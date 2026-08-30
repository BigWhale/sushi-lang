"""Constants for runtime operations."""
from __future__ import annotations

# errno constants (from errno.h)
# These values are standard across most UNIX-like systems
# Used for mapping system errors to Sushi FileError enum variants
ERRNO_EPERM = 1           # Operation not permitted
ERRNO_ENOENT = 2          # No such file or directory
ERRNO_EIO = 5             # I/O error
ERRNO_EACCES = 13         # Permission denied
ERRNO_EEXIST = 17         # File exists
ERRNO_ENOTDIR = 20        # Not a directory
ERRNO_EISDIR = 21         # Is a directory
ERRNO_EMFILE = 24         # Too many open files
ERRNO_ENOSPC = 28         # No space left on device
ERRNO_EBADF = 9           # Bad file descriptor
ERRNO_EINTR = 4           # Interrupted system call
ERRNO_ENFILE = 23         # Too many open files in the system
ERRNO_EINVAL = 22         # Invalid argument
ERRNO_EPIPE = 32          # Broken pipe


def errno_to_file_error_table(is_linux: bool) -> dict[int, int]:
    """errno value -> FileError variant tag.

    The tag order is CollectorPass._register_predefined_enums. ENAMETOOLONG and
    ELOOP do not agree between macOS (63, 62) and Linux (36, 40); the shared
    constants above do. An unmapped errno maps to ERRNO_DEFAULT_FILE_ERROR.
    """
    enametoolong = 36 if is_linux else 63
    eloop = 40 if is_linux else 62
    return {
        ERRNO_ENOENT: 0,     # NotFound
        ERRNO_EPERM: 1,      # PermissionDenied
        ERRNO_EACCES: 1,     # PermissionDenied
        ERRNO_EEXIST: 2,     # AlreadyExists
        ERRNO_EISDIR: 3,     # IsDirectory
        ERRNO_ENOSPC: 4,     # DiskFull
        ERRNO_EMFILE: 5,     # TooManyOpen
        enametoolong: 6,     # InvalidPath
        ERRNO_ENOTDIR: 6,    # InvalidPath
        eloop: 6,            # InvalidPath
        ERRNO_EIO: 7,        # IOError
    }


ERRNO_DEFAULT_FILE_ERROR = 8  # Other


def errno_to_net_error_table(is_linux: bool) -> dict[int, int]:
    """errno value -> NetError variant tag.

    The tag order is CollectorPass._register_predefined_enums. Almost nothing
    agrees between the platforms here: only EPIPE, EBADF, EINTR, EACCES, EPERM,
    EMFILE and ENFILE share a number, so every other value is a pair. Both
    columns came from an errno.h probe (2026-08-30). An unmapped errno maps to
    ERRNO_DEFAULT_NET_ERROR.

    EAGAIN maps to TimedOut because every socket <net/socket> creates is
    BLOCKING. On a blocking socket EAGAIN can only mean the SO_RCVTIMEO or
    SO_SNDTIMEO the caller asked for expired -- that is how POSIX reports a
    socket timeout, connect() reporting ETIMEDOUT instead. When non-blocking
    mode arrives, append WouldBlock as a NEW tag and choose per call site; the
    tags below are an ABI and never move.

    EBADF maps to Closed so that an operation on a descriptor that was already
    closed names the condition rather than falling through to Other.
    """
    if is_linux:
        erefused, etimedout = 111, 110
        einuse, enotavail = 98, 99
        enetunreach, enetdown, enetreset = 101, 100, 102
        ehostunreach = 113
        ereset, eabort = 104, 103
        enotconn = 107
        eagain, einprogress = 11, 115
        eafnosupport, eprotonosupport = 97, 93
        edestaddrreq, emsgsize = 89, 90
    else:
        erefused, etimedout = 61, 60
        einuse, enotavail = 48, 49
        enetunreach, enetdown, enetreset = 51, 50, 52
        ehostunreach = 65
        ereset, eabort = 54, 53
        enotconn = 57
        eagain, einprogress = 35, 36
        eafnosupport, eprotonosupport = 47, 43
        edestaddrreq, emsgsize = 39, 40
    return {
        erefused: 0,             # ConnectionRefused
        ereset: 1,               # ConnectionReset
        eabort: 1,               # ConnectionReset
        etimedout: 2,            # TimedOut
        eagain: 2,               # TimedOut
        einprogress: 2,          # TimedOut
        ERRNO_EPIPE: 3,          # Closed
        enotconn: 3,             # Closed
        ERRNO_EBADF: 3,          # Closed
        einuse: 4,               # AddressInUse
        enotavail: 5,            # AddressNotAvailable
        enetunreach: 6,          # NetworkUnreachable
        enetdown: 6,             # NetworkUnreachable
        enetreset: 6,            # NetworkUnreachable
        ehostunreach: 7,         # HostUnreachable
        ERRNO_EPERM: 9,          # PermissionDenied
        ERRNO_EACCES: 9,         # PermissionDenied
        ERRNO_EMFILE: 10,        # TooManyOpen
        ERRNO_ENFILE: 10,        # TooManyOpen
        eafnosupport: 11,        # InvalidAddress
        eprotonosupport: 11,     # InvalidAddress
        edestaddrreq: 11,        # InvalidAddress
        ERRNO_EINVAL: 11,        # InvalidAddress
        ERRNO_EINTR: 12,         # Interrupted
        emsgsize: 13,            # MessageTooLarge
    }


# Tag 8 is ResolveFailed, which no errno reaches: getaddrinfo answers with an
# EAI_* code and not with errno, so it is set by emit_gai_err alone.
ERRNO_DEFAULT_NET_ERROR = 14  # Other

FORMAT_STRINGS = {
    "i32": "%d",
    "i64": "%lld",
    "u32": "%u",
    "u64": "%llu",
    "str": "%s",
    "f32": "%g",
    "f64": "%g",
    "bool_true": "true",
    "bool_false": "false",
}
