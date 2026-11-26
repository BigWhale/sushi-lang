"""The libc table: one C prototype for each libc symbol that the compiler declares.

The table is data and imports no llvmlite, so the semantic side reads the reserved
signatures (`malloc`, `free`, `exit`) from it (#1099). `libc_declarations.py` lowers each
row to an LLVM function type.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LibcSignature:
    """One libc prototype, in C terms."""
    ret: str
    params: tuple[str, ...]
    var_arg: bool = False


def _c(ret: str, *params: str, var_arg: bool = False) -> LibcSignature:
    return LibcSignature(ret, params, var_arg)


_CTYPE = _c("int", "int")

#: Every libc symbol that the compiler declares. The key is the C name; a platform
#: variant of a symbol (`stat$INODE64`) is declared with the signature of its C name.
LIBC_SIGNATURES: dict[str, LibcSignature] = {
    # Memory and process
    "malloc": _c("void*", "size_t"),
    "free": _c("void", "void*"),
    "realloc": _c("void*", "void*", "size_t"),
    "exit": _c("void", "int"),
    "__error": _c("int*"),
    "__errno_location": _c("int*"),
    # Strings and formatting
    "strlen": _c("size_t", "const char*"),
    "memcmp": _c("int", "const void*", "const void*", "size_t"),
    "strtol": _c("long", "const char*", "char**", "int"),
    "strtoll": _c("long long", "const char*", "char**", "int"),
    "strtod": _c("double", "const char*", "char**"),
    "sprintf": _c("int", "char*", "const char*", var_arg=True),
    "toupper": _CTYPE,
    "tolower": _CTYPE,
    "isspace": _CTYPE,
    "isdigit": _CTYPE,
    "isalnum": _CTYPE,
    # stdio
    "fprintf": _c("int", "FILE*", "const char*", var_arg=True),
    "fwrite": _c("size_t", "const void*", "size_t", "size_t", "FILE*"),
    "setvbuf": _c("int", "FILE*", "char*", "int", "size_t"),
    # Files and descriptors
    "stat": _c("int", "const char*", "struct stat*"),
    "lstat": _c("int", "const char*", "struct stat*"),
    "write": _c("ssize_t", "int", "const void*", "size_t"),
    "dup": _c("int", "int"),
    "close": _c("int", "int"),
    "opendir": _c("DIR*", "const char*"),
    "readdir": _c("struct dirent*", "DIR*"),
    "closedir": _c("int", "DIR*"),
    # Sockets
    "socket": _c("int", "int", "int", "int"),
    "connect": _c("int", "int", "const struct sockaddr*", "socklen_t"),
    "bind": _c("int", "int", "const struct sockaddr*", "socklen_t"),
    "listen": _c("int", "int", "int"),
    "accept": _c("int", "int", "struct sockaddr*", "socklen_t*"),
    "send": _c("ssize_t", "int", "const void*", "size_t", "int"),
    "recv": _c("ssize_t", "int", "void*", "size_t", "int"),
    "sendto": _c("ssize_t", "int", "const void*", "size_t", "int",
                 "const struct sockaddr*", "socklen_t"),
    "recvfrom": _c("ssize_t", "int", "void*", "size_t", "int",
                   "struct sockaddr*", "socklen_t*"),
    "setsockopt": _c("int", "int", "int", "int", "const void*", "socklen_t"),
    "getsockname": _c("int", "int", "struct sockaddr*", "socklen_t*"),
    "getpeername": _c("int", "int", "struct sockaddr*", "socklen_t*"),
    "shutdown": _c("int", "int", "int"),
    "getaddrinfo": _c("int", "const char*", "const char*", "const struct addrinfo*",
                      "struct addrinfo**"),
    "freeaddrinfo": _c("void", "struct addrinfo*"),
    "getnameinfo": _c("int", "const struct sockaddr*", "socklen_t", "char*", "socklen_t",
                      "char*", "socklen_t", "int"),
}
