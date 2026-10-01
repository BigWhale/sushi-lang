"""C Library Function Declarations

`LIBC_SIGNATURES` is the one table of every libc symbol that the compiler declares: the
stdlib generators and the back-end runtime both read it, and the semantic side reads the
reserved signatures (`malloc`, `free`, `exit`) from it (#1099).

`declare_extern` is the one accessor that declares an external function (#910). A libc
symbol goes through `declare_libc`, which reads the table. A name with the `llvm.` prefix
(an LLVM intrinsic) or the `llvm_` prefix (a compiler helper, whose body the compiler
emits) is not libc and goes to `declare_extern` directly. A user `unsafe external` goes to
`declare_extern` from `backend/runtime/externs/user_externs.py`.

Each declaration is independent: when the module already holds the symbol with another
function type, the caller gets a bitcast of it to its own type, never the other type.

tests/unit/test_extern_declaration_is_one_seam.py is the gate.
"""

from dataclasses import dataclass
from typing import Any, Sequence

import llvmlite.ir as ir

_i8 = ir.IntType(8)
_i8_ptr = _i8.as_pointer()
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)  # size_t and long are i64 on 64-bit systems
_f64 = ir.DoubleType()
_void = ir.VoidType()


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


_INT32 = ("int", "unsigned", "pid_t", "uid_t", "mode_t", "socklen_t", "clockid_t")
_INT64 = ("long", "long long", "size_t", "ssize_t", "off_t")
_BYTE_POINTERS = (
    "char*", "const char*", "void*", "const void*", "FILE*", "DIR*", "struct dirent*",
    "struct stat*", "struct sockaddr*", "const struct sockaddr*", "struct addrinfo*",
    "const struct addrinfo*", "posix_spawn_file_actions_t*",
    "const posix_spawn_file_actions_t*", "const posix_spawnattr_t*",
)

#: How each C type of the table lowers to LLVM.
C_TYPE_LOWERING: dict[str, ir.Type] = {
    "void": _void,
    "double": _f64,
    **{name: _i32 for name in _INT32},
    **{name: _i64 for name in _INT64},
    **{name: _i8_ptr for name in _BYTE_POINTERS},
    **{name: _i32.as_pointer() for name in ("int*", "pid_t*", "socklen_t*")},
    **{name: _i8_ptr.as_pointer() for name in ("char**", "struct addrinfo**")},
}


def _lower(c_type: str) -> ir.Type:
    if c_type in ("struct timespec*", "const struct timespec*"):
        from sushi_lang.sushi_stdlib.src.type_definitions import get_timespec_type
        return get_timespec_type().as_pointer()
    return C_TYPE_LOWERING[c_type]


def libc_function_type(name: str) -> ir.FunctionType:
    """The LLVM function type of the libc symbol `name`, from the table."""
    sig = LIBC_SIGNATURES[name]
    return ir.FunctionType(_lower(sig.ret), [_lower(p) for p in sig.params],
                           var_arg=sig.var_arg)


def as_callee(existing: Any, fn_ty: ir.FunctionType) -> Any:
    """`existing` as a callee of `fn_ty`: itself when its type is `fn_ty`, else a bitcast.

    A call goes through the type of its own declaration. Another declaration of the
    same symbol in the module does not change it (#1099).
    """
    if isinstance(existing, ir.Function) and existing.function_type == fn_ty:
        return existing
    cast = existing.bitcast(fn_ty.as_pointer())
    cast.function_type = fn_ty
    return cast


def declare_extern(
    module: ir.Module,
    name: str,
    ret: ir.Type,
    args: Sequence[ir.Type],
    var_arg: bool = False,
) -> Any:
    """Declare `ret name(args...)` in the module, or give the global of that name as a
    callee of that type."""
    fn_ty = ir.FunctionType(ret, list(args), var_arg=var_arg)
    existing = module.globals.get(name)
    if existing is None:
        return ir.Function(module, fn_ty, name=name)
    return as_callee(existing, fn_ty)


def declare_libc(module: ir.Module, name: str, link_name: str | None = None) -> Any:
    """Declare the libc symbol `name` with its signature from `LIBC_SIGNATURES`.

    `link_name` is a platform variant of the symbol (`stat$INODE64`)."""
    fn_ty = libc_function_type(name)
    return declare_extern(module, link_name or name, fn_ty.return_type, fn_ty.args,
                          var_arg=fn_ty.var_arg)


def declare_math(module: ir.Module, name: str, arity: int) -> Any:
    """An f64 function of `arity` arguments: an LLVM intrinsic, or a libc function."""
    if name.startswith("llvm."):
        return declare_extern(module, name, _f64, [_f64] * arity)
    return declare_libc(module, name)


def declare_malloc(module: ir.Module) -> Any:
    return declare_libc(module, "malloc")


def declare_free(module: ir.Module) -> Any:
    return declare_libc(module, "free")


def declare_realloc(module: ir.Module) -> Any:
    return declare_libc(module, "realloc")


def declare_memcpy(module: ir.Module) -> Any:
    """The LLVM memcpy intrinsic: void llvm.memcpy.p0i8.p0i8.i64(i8*, i8*, i64, i1)."""
    operands = [_i8_ptr, _i8_ptr, _i64]
    name = ".".join(["llvm.memcpy"] + [t.intrinsic_name for t in operands])
    return declare_extern(module, name, _void, operands + [ir.IntType(1)])


def declare_strlen(module: ir.Module) -> Any:
    """i32 llvm_strlen(i8*); the body is emitted during the final compilation."""
    return declare_extern(module, "llvm_strlen", _i32, [_i8_ptr])


def declare_strtol(module: ir.Module) -> Any:
    return declare_libc(module, "strtol")


def declare_strtoll(module: ir.Module) -> Any:
    return declare_libc(module, "strtoll")


def declare_strtod(module: ir.Module) -> Any:
    return declare_libc(module, "strtod")


def declare_sprintf(module: ir.Module) -> Any:
    return declare_libc(module, "sprintf")


def declare_fprintf(module: ir.Module) -> Any:
    return declare_libc(module, "fprintf")


def declare_fread(module: ir.Module) -> Any:
    return declare_libc(module, "fread")


def declare_fclose(module: ir.Module) -> Any:
    return declare_libc(module, "fclose")


def declare_fseek(module: ir.Module) -> Any:
    return declare_libc(module, "fseek")


def declare_ftell(module: ir.Module) -> Any:
    return declare_libc(module, "ftell")


def declare_exit(module: ir.Module) -> Any:
    return declare_libc(module, "exit")


def declare_errno_location(module: ir.Module) -> Any:
    """int* __error() (macOS) / __errno_location() (Linux)."""
    from sushi_lang.backend.platform_detect import get_current_platform

    if get_current_platform().is_linux:
        return declare_libc(module, "__errno_location")
    return declare_libc(module, "__error")
