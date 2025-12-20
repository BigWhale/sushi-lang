"""
Darwin/macOS-specific stdio handle declarations.

On macOS, the C standard library exposes stdin/stdout/stderr through
special global pointers with double-underscore names:
- __stdinp  (stdin)
- __stdoutp (stdout)
- __stderrp (stderr)

This is different from Linux which uses the names directly (stdin, stdout, stderr).
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src._platform.posix import stdio as posix_stdio


def get_stdin_handle_name() -> str:
    """Get the platform-specific name for stdin handle."""
    return "__stdinp"


def get_stdout_handle_name() -> str:
    """Get the platform-specific name for stdout handle."""
    return "__stdoutp"


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle."""
    return "__stderrp"


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct)."""
    return posix_stdio.declare_file_type(module)


def declare_stdin_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer (macOS __stdinp)."""
    return posix_stdio.declare_stdin_handle(module, get_stdin_handle_name())


def declare_stdout_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer (macOS __stdoutp)."""
    return posix_stdio.declare_stdout_handle(module, get_stdout_handle_name())


def declare_stderr_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer (macOS __stderrp)."""
    return posix_stdio.declare_stderr_handle(module, get_stderr_handle_name())
