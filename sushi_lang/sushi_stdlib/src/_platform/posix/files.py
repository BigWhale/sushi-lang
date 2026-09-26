"""Common POSIX file system function declarations."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_extern

_i8_ptr = ir.IntType(8).as_pointer()
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)


def declare_stat(module: ir.Module, name: str = "stat") -> ir.Function:
    """Declare POSIX stat() syscall. The symbol name is a parameter because
    macOS x86_64 exports the 64-bit-inode layout as stat$INODE64."""
    return declare_extern(module, name, _i32, [_i8_ptr, _i8_ptr])


def declare_lstat(module: ir.Module, name: str = "lstat") -> ir.Function:
    """Declare POSIX lstat() syscall; the name parameter mirrors declare_stat."""
    return declare_extern(module, name, _i32, [_i8_ptr, _i8_ptr])


def declare_access(module: ir.Module) -> ir.Function:
    """Declare POSIX access() syscall."""
    return declare_extern(module, "access", _i32, [_i8_ptr, _i32])


def declare_unlink(module: ir.Module) -> ir.Function:
    """Declare POSIX unlink() syscall."""
    return declare_extern(module, "unlink", _i32, [_i8_ptr])


def declare_rename(module: ir.Module) -> ir.Function:
    """Declare POSIX rename() syscall."""
    return declare_extern(module, "rename", _i32, [_i8_ptr, _i8_ptr])


def declare_open(module: ir.Module) -> ir.Function:
    """Declare POSIX open() syscall: `int open(const char *, int, ...)`.

    VARIADIC, as C declares it. Declared with a fixed third parameter, the mode went in a
    register while Apple arm64 expects a variadic argument on the stack, so the callee read
    the mode from the wrong place -- `copy()` produced whatever happened to be there (#363).
    """
    return declare_extern(module, "open", _i32, [_i8_ptr, _i32], var_arg=True)


def declare_read(module: ir.Module) -> ir.Function:
    """Declare POSIX read() syscall."""
    return declare_extern(module, "read", _i64, [_i32, _i8_ptr, _i64])


def declare_write(module: ir.Module) -> ir.Function:
    """Declare POSIX write() syscall."""
    return declare_extern(module, "write", _i64, [_i32, _i8_ptr, _i64])


def declare_pread(module: ir.Module) -> ir.Function:
    """Declare POSIX pread(): `ssize_t pread(int, void *, size_t, off_t)`.

    The POSITIONAL read. The offset is an argument, so the descriptor's own file position
    never moves and two readers of one descriptor cannot race over it -- the reason every
    language that supports concurrent file I/O converged on this primitive rather than on
    a new kind of type (HANDLES.md, Phase 8).

    `off_t` is 64-bit on both supported platforms: probe P6 measured 8 bytes on macOS
    arm64 and on Linux x86_64, where plain `pread` already IS the wide-offset entry point.
    """
    return declare_extern(module, "pread", _i64, [_i32, _i8_ptr, _i64, _i64])


def declare_pwrite(module: ir.Module) -> ir.Function:
    """Declare POSIX pwrite(): `ssize_t pwrite(int, const void *, size_t, off_t)`.

    The positional write, and `declare_pread`'s twin in every respect.
    """
    return declare_extern(module, "pwrite", _i64, [_i32, _i8_ptr, _i64, _i64])


def declare_lseek(module: ir.Module) -> ir.Function:
    """Declare POSIX lseek(): `off_t lseek(int, off_t, int)`.

    `off_t` is 64-bit on both supported platforms, which probe P6 measured for `pread`;
    the same answer holds here, so the offset and the result are both i64.
    """
    return declare_extern(module, "lseek", _i64, [_i32, _i64, _i32])


def declare_isatty(module: ir.Module) -> ir.Function:
    """Declare POSIX isatty(): `int isatty(int)`.

    Asking cannot fail in any way a caller can act on -- a descriptor that is not a
    terminal and a descriptor that is not open both answer 0 -- so `fd_isatty` gives a
    bare bool rather than a Result.
    """
    return declare_extern(module, "isatty", _i32, [_i32])


def declare_dup(module: ir.Module) -> ir.Function:
    """Declare POSIX dup(): `int dup(int)`.

    A SECOND descriptor over the SAME open file description. The offset is shared, so
    this is the shared-listener primitive and not the answer for concurrent reads of one
    file -- `pread`/`pwrite` are that. `.share()` is built on it in Phase 8.
    """
    return declare_extern(module, "dup", _i32, [_i32])


def declare_close(module: ir.Module) -> ir.Function:
    """Declare POSIX close() syscall."""
    return declare_extern(module, "close", _i32, [_i32])


def declare_mkdir(module: ir.Module) -> ir.Function:
    """Declare POSIX mkdir() syscall."""
    return declare_extern(module, "mkdir", _i32, [_i8_ptr, _i32])


def declare_rmdir(module: ir.Module) -> ir.Function:
    """Declare POSIX rmdir() syscall."""
    return declare_extern(module, "rmdir", _i32, [_i8_ptr])


def declare_opendir(module: ir.Module) -> ir.Function:
    """Declare POSIX opendir(): DIR* is opaque, an i8* here."""
    return declare_extern(module, "opendir", _i8_ptr, [_i8_ptr])


def declare_readdir(module: ir.Module, name: str = "readdir") -> ir.Function:
    """Declare POSIX readdir(). The symbol name is a parameter because macOS
    x86_64 exports the 64-bit-inode layout as readdir$INODE64."""
    return declare_extern(module, name, _i8_ptr, [_i8_ptr])


def declare_closedir(module: ir.Module) -> ir.Function:
    """Declare POSIX closedir()."""
    return declare_extern(module, "closedir", _i32, [_i8_ptr])
