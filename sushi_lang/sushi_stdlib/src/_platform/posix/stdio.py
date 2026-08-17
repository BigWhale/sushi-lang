"""POSIX stdio handle declarations with platform-specific symbol resolution."""

import llvmlite.ir as ir


def declare_file_type(module: ir.Module) -> ir.PointerType:
    """Get the FILE* type (opaque pointer to FILE struct)."""
    return ir.IntType(8).as_pointer()


def declare_stdin_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stdin as external global FILE* pointer."""
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdin_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdin_global.linkage = 'external'
        return stdin_global
    return module.globals[handle_name]


def declare_stdout_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stdout as external global FILE* pointer."""
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stdout_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stdout_global.linkage = 'external'
        return stdout_global
    return module.globals[handle_name]


def declare_stderr_handle(module: ir.Module, handle_name: str) -> ir.GlobalVariable:
    """Declare stderr as external global FILE* pointer."""
    if handle_name not in module.globals:
        file_ptr_ty = declare_file_type(module)
        stderr_global = ir.GlobalVariable(module, file_ptr_ty, name=handle_name)
        stderr_global.linkage = 'external'
        return stderr_global
    return module.globals[handle_name]
