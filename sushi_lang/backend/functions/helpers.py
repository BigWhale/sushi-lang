"""Helper functions for LLVM function management."""
from __future__ import annotations
from llvmlite import ir


def declare_stdlib_function(
    module: ir.Module,
    func_name: str,
    return_type: ir.Type,
    param_types: list[ir.Type]
) -> ir.Function:
    """Declare an external stdlib function."""
    if func_name in module.globals:
        existing = module.globals[func_name]
        if isinstance(existing, ir.Function):
            return existing

    fn_type = ir.FunctionType(return_type, param_types)
    func = ir.Function(module, fn_type, name=func_name)
    return func
