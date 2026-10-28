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


_MATH_1 = _c("double", "double")
_MATH_2 = _c("double", "double", "double")
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
    "fread": _c("size_t", "void*", "size_t", "size_t", "FILE*"),
    "fclose": _c("int", "FILE*"),
    "fseek": _c("int", "FILE*", "long", "int"),
    "ftell": _c("long", "FILE*"),
    "setvbuf": _c("int", "FILE*", "char*", "int", "size_t"),
    "tmpfile": _c("FILE*"),
    "fileno": _c("int", "FILE*"),
    # Files and descriptors
    "stat": _c("int", "const char*", "struct stat*"),
    "lstat": _c("int", "const char*", "struct stat*"),
    "access": _c("int", "const char*", "int"),
    "unlink": _c("int", "const char*"),
    "rename": _c("int", "const char*", "const char*"),
    "open": _c("int", "const char*", "int", var_arg=True),
    "read": _c("ssize_t", "int", "void*", "size_t"),
    "write": _c("ssize_t", "int", "const void*", "size_t"),
    "pread": _c("ssize_t", "int", "void*", "size_t", "off_t"),
    "pwrite": _c("ssize_t", "int", "const void*", "size_t", "off_t"),
    "lseek": _c("off_t", "int", "off_t", "int"),
    "isatty": _c("int", "int"),
    "dup": _c("int", "int"),
    "close": _c("int", "int"),
    "mkdir": _c("int", "const char*", "mode_t"),
    "rmdir": _c("int", "const char*"),
    "opendir": _c("DIR*", "const char*"),
    "readdir": _c("struct dirent*", "DIR*"),
    "closedir": _c("int", "DIR*"),
    # Environment, process, random, time
    "getenv": _c("char*", "const char*"),
    "setenv": _c("int", "const char*", "const char*", "int"),
    "getcwd": _c("char*", "char*", "size_t"),
    "chdir": _c("int", "const char*"),
    "getpid": _c("pid_t"),
    "getuid": _c("uid_t"),
    "waitpid": _c("pid_t", "pid_t", "int*", "int"),
    "posix_spawnp": _c("int", "pid_t*", "const char*", "const posix_spawn_file_actions_t*",
                       "const posix_spawnattr_t*", "char**", "char**"),
    "posix_spawn_file_actions_init": _c("int", "posix_spawn_file_actions_t*"),
    "posix_spawn_file_actions_adddup2": _c("int", "posix_spawn_file_actions_t*",
                                           "int", "int"),
    "posix_spawn_file_actions_destroy": _c("int", "posix_spawn_file_actions_t*"),
    "random": _c("long"),
    "srandom": _c("void", "unsigned"),
    "nanosleep": _c("int", "const struct timespec*", "struct timespec*"),
    "clock_gettime": _c("int", "clockid_t", "struct timespec*"),
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
    # <math> functions that libc provides
    "tan": _MATH_1, "asin": _MATH_1, "acos": _MATH_1, "atan": _MATH_1,
    "sinh": _MATH_1, "cosh": _MATH_1, "tanh": _MATH_1,
    "atan2": _MATH_2, "hypot": _MATH_2,
}
