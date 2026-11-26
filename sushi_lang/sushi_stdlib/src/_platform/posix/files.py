"""Common POSIX file system function declarations."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc


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
