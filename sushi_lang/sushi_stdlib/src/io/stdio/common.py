"""
Common utilities and infrastructure for stdio module.

This module provides stdio-specific utilities, delegating to platform-specific
implementations for stdio handle declarations via the _platform abstraction layer.

Platform-specific implementations:
- _platform/darwin/stdio.py: macOS stdio handles (__stdinp, __stdoutp, __stderrp)
- _platform/linux/stdio.py: Linux stdio handles (stdin, stdout, stderr)
"""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module

# Get platform-specific stdio module (darwin, linux, etc.)
_platform_stdio = get_platform_module('stdio')


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct).

    Args:
        module: The LLVM module.

    Returns:
        FILE* type (i8* - opaque pointer).
    """
    return _platform_stdio.declare_file_type(module)


def declare_stdin_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer.

    Delegates to platform-specific implementation (currently darwin).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stdin global variable.
    """
    return _platform_stdio.declare_stdin_handle(module)


def declare_stdout_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer.

    Delegates to platform-specific implementation (currently darwin).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stdout global variable.
    """
    return _platform_stdio.declare_stdout_handle(module)


def declare_stderr_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer.

    Delegates to platform-specific implementation (currently darwin).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stderr global variable.
    """
    return _platform_stdio.declare_stderr_handle(module)


# Expose platform-specific handle name getters for use by other modules
def get_stdin_handle_name() -> str:
    """Get the platform-specific name for stdin handle."""
    return _platform_stdio.get_stdin_handle_name()


def get_stdout_handle_name() -> str:
    """Get the platform-specific name for stdout handle."""
    return _platform_stdio.get_stdout_handle_name()


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle."""
    return _platform_stdio.get_stderr_handle_name()

