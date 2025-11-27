"""
POSIX stdio handle declarations with platform-specific symbol resolution.

This module provides common stdio functionality across POSIX systems.
Platform-specific symbol names are provided by darwin/stdio.py and linux/stdio.py.
"""

import llvmlite.ir as ir


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct).

    Args:
        module: The LLVM module.

    Returns:
        FILE* type (i8* - opaque pointer).
    """
    return ir.IntType(8).as_pointer()


def declare_stdin_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer.

    Args:
        module: The LLVM module to declare the global in.
        handle_name: Platform-specific symbol name (e.g., "stdin" or "__stdinp")

    Returns:
        The stdin global variable.
    """
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdin_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdin_global.linkage = 'external'
        return stdin_global
    return module.globals[handle_name]


def declare_stdout_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer.

    Args:
        module: The LLVM module to declare the global in.
        handle_name: Platform-specific symbol name (e.g., "stdout" or "__stdoutp")

    Returns:
        The stdout global variable.
    """
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdout_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdout_global.linkage = 'external'
        return stdout_global
    return module.globals[handle_name]


def declare_stderr_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer.

    Args:
        module: The LLVM module to declare the global in.
        handle_name: Platform-specific symbol name (e.g., "stderr" or "__stderrp")

    Returns:
        The stderr global variable.
    """
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stderr_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stderr_global.linkage = 'external'
        return stderr_global
    return module.globals[handle_name]
