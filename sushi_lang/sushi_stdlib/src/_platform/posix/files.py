"""Common POSIX file system function declarations."""
from llvmlite import ir
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_libc


def declare_write(module: ir.Module) -> ir.Function:
    """Declare POSIX write() syscall."""
    return declare_libc(module, "write")
