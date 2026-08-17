"""Common utilities and infrastructure for stdio module."""

import llvmlite.ir as ir
from sushi_lang.sushi_stdlib.src._platform import get_platform_module

_platform_stdio = get_platform_module('stdio')


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct)."""
    return _platform_stdio.declare_file_type(module)


def declare_stdin_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer."""
    return _platform_stdio.declare_stdin_handle(module)


def declare_stdout_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer."""
    return _platform_stdio.declare_stdout_handle(module)


def declare_stderr_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer."""
    return _platform_stdio.declare_stderr_handle(module)


def get_stdin_handle_name() -> str:
    """Get the platform-specific name for stdin handle."""
    return _platform_stdio.get_stdin_handle_name()


def get_stdout_handle_name() -> str:
    """Get the platform-specific name for stdout handle."""
    return _platform_stdio.get_stdout_handle_name()


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle."""
    return _platform_stdio.get_stderr_handle_name()

