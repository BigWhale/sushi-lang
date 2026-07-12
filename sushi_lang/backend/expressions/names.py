"""
Name (variable reference) emission for the Sushi language compiler.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import Name
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_name(codegen: 'LLVMCodegen', expr: Name, to_i1: bool) -> ir.Value:
    """Emit variable or constant reference.

    First checks for global constants, then string constants, then local variables.
    For reference parameters, automatically dereferences them when used as values.

    Args:
        codegen: The LLVM codegen instance.
        expr: The name expression.
        to_i1: Whether to convert result to i1.

    Returns:
        The loaded variable or constant value.
    """
    # Check for math module constants (PI, E, TAU)
    if expr.id in {'PI', 'E', 'TAU'}:
        from sushi_lang.sushi_stdlib.src import math as math_module
        if math_module.is_builtin_math_constant(expr.id):
            _, value = math_module.get_builtin_math_constant_value(expr.id)
            f64_value = ir.Constant(ir.DoubleType(), value)
            return codegen.utils.as_i1(f64_value) if to_i1 else f64_value

    # Check for global constants (including string constants stored as fat pointer structs)
    if expr.id in codegen.constants:
        global_const = codegen.constants[expr.id]
        v = codegen.builder.load(global_const, name=expr.id)
        return codegen.utils.as_i1(v) if to_i1 else v

    # Try local variables
    try:
        from sushi_lang.backend.expressions import type_utils
        slot = codegen.memory.find_local_slot(expr.id)
        v = type_utils.load_with_reference_handling(codegen, expr.id, slot)
        return codegen.utils.as_i1(v) if to_i1 else v
    except Exception:
        # Not a local: a bare reference to a top-level function is a first-class
        # function value -> a non-capturing fat pointer {thunk, null, null}. The
        # thunk bridges the bare fn into the uniform env-passing indirect ABI.
        llvm_fn = codegen.funcs.get(expr.id)
        if llvm_fn is not None:
            from sushi_lang.backend.runtime import closures
            return closures.materialize_function_ref(codegen, llvm_fn)
        # Variable/constant not found (should be caught in semantic analysis)
        raise_internal_error("CE0055", name=expr.id)

