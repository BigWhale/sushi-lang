"""
Darwin/macOS-specific stdio handle declarations.

On macOS, the C standard library exposes stdin/stdout/stderr through
special global pointers with double-underscore names:
- __stdinp  (stdin)
- __stdoutp (stdout)
- __stderrp (stderr)

This is different from Linux which uses the names directly (stdin, stdout, stderr).

This module is part of the platform abstraction layer and is selected dynamically
by stdlib.src._platform.get_platform_module('stdio') when building on macOS.
"""

import llvmlite.ir as ir


def get_stdin_handle_name() -> str:
    """Get the platform-specific name for stdin handle.

    Returns:
        "__stdinp" on macOS/Darwin
    """
    return "__stdinp"


def get_stdout_handle_name() -> str:
    """Get the platform-specific name for stdout handle.

    Returns:
        "__stdoutp" on macOS/Darwin
    """
    return "__stdoutp"


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle.

    Returns:
        "__stderrp" on macOS/Darwin
    """
    return "__stderrp"


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct).

    Args:
        module: The LLVM module.

    Returns:
        FILE* type (i8* - opaque pointer).
    """
    return ir.IntType(8).as_pointer()


def declare_stdin_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer (macOS __stdinp).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stdin global variable.
    """
    handle_name = get_stdin_handle_name()
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdin_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdin_global.linkage = 'external'
        return stdin_global
    return module.globals[handle_name]


def declare_stdout_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer (macOS __stdoutp).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stdout global variable.
    """
    handle_name = get_stdout_handle_name()
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdout_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdout_global.linkage = 'external'
        return stdout_global
    return module.globals[handle_name]


def declare_stderr_handle(module: ir.Module) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer (macOS __stderrp).

    Args:
        module: The LLVM module to declare the global in.

    Returns:
        The stderr global variable.
    """
    handle_name = get_stderr_handle_name()
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stderr_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stderr_global.linkage = 'external'
        return stderr_global
    return module.globals[handle_name]
