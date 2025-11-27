"""
Linux-specific stdio handle declarations.

On Linux (glibc and musl), the C standard library exposes stdin/stdout/stderr
as direct global symbols without the double-underscore prefix:
- stdin  (not __stdinp)
- stdout (not __stdoutp)
- stderr (not __stderrp)
"""

import llvmlite.ir as ir
from stdlib.src._platform.posix import stdio as posix_stdio


def get_stdin_handle_name() -> str:
    """Get the platform-specific name for stdin handle."""
    return "stdin"


def get_stdout_handle_name() -> str:
    """Get the platform-specific name for stdout handle."""
    return "stdout"


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle."""
    return "stderr"


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct)."""
    return posix_stdio.declare_file_type(module)


def declare_stdin_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer (Linux stdin)."""
    return posix_stdio.declare_stdin_handle(module, get_stdin_handle_name())


def declare_stdout_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer (Linux stdout)."""
    return posix_stdio.declare_stdout_handle(module, get_stdout_handle_name())


def declare_stderr_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer (Linux stderr)."""
    return posix_stdio.declare_stderr_handle(module, get_stderr_handle_name())
