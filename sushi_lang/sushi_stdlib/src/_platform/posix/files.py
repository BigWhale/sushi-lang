"""Common POSIX file system function declarations."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc

_i8_ptr = ir.IntType(8).as_pointer()
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)


def declare_write(module: ir.Module) -> ir.Function:
    """Declare POSIX write() syscall."""
    return declare_libc(module, "write")


def declare_dup(module: ir.Module) -> ir.Function:
    """Declare POSIX dup(): `int dup(int)`.

    A SECOND descriptor over the SAME open file description. The offset is shared, so
    this is the shared-listener primitive and not the answer for concurrent reads of one
    file -- `pread`/`pwrite` are that. `.share()` is built on it in Phase 8.
    """
    return declare_libc(module, "dup")


def declare_close(module: ir.Module) -> ir.Function:
    """Declare POSIX close() syscall."""
    return declare_libc(module, "close")


def declare_opendir(module: ir.Module) -> ir.Function:
    """Declare POSIX opendir(): DIR* is opaque, an i8* here."""
    return declare_libc(module, "opendir")


def declare_readdir(module: ir.Module, name: str = "readdir") -> ir.Function:
    """Declare POSIX readdir(). The symbol name is a parameter because macOS
    x86_64 exports the 64-bit-inode layout as readdir$INODE64."""
    return declare_libc(module, "readdir", name)


def declare_closedir(module: ir.Module) -> ir.Function:
    """Declare POSIX closedir()."""
    return declare_libc(module, "closedir")
