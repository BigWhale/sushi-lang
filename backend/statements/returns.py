"""
Return statement emission for the Sushi language compiler.

This module handles the generation of LLVM IR for return statements with
proper RAII cleanup of resources before exiting functions.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import Return, Expr


def _extract_return_variables(expr: 'Expr') -> set[str]:
    """Extract variable names from return expression.

    Returns the names of variables that are being returned, so they
    can be excluded from RAII cleanup (move semantics).

    Examples:
        return Result.Ok(container)  → {'container'}
        return Result.Ok(x)          → {'x'}
        return Result.Ok(~)          → {}
        return Result.Err()          → {}

    Args:
        expr: The return expression to analyze.

    Returns:
        Set of variable names being returned.
    """
    from semantics.ast import EnumConstructor, DotCall, Name

    if isinstance(expr, EnumConstructor):
        # Result.Ok(value) or Result.Err()
        if expr.args:
            # Recursively extract from the first argument
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, DotCall):
        # DotCall node (unified X.Y(args)) - check if it's an enum constructor
        # For returns, this is typically Result.Ok(value) or Result.Err()
        if expr.args:
            # Recursively extract from the first argument
            return _extract_return_variables(expr.args[0])
    elif isinstance(expr, Name):
        # Simple variable reference
        return {expr.id}

    # Other expressions (literals, method calls, etc.) don't have variables to move
    return set()


def emit_return(codegen: 'LLVMCodegen', stmt: 'Return') -> None:
    """Emit return statement with Result<T> value or bare value for extension methods.

    For regular functions: stmt.value is Ok(expr) or Err(), which emit_expr
    will convert to a Result struct {i1 is_ok, T value}.

    For extension methods: stmt.value is a bare expression (e.g., self + value),
    which emit_expr will evaluate directly.

    RAII: Generates cleanup code for all active scopes before the return instruction.
    Move semantics: Variables being returned are marked as moved to prevent cleanup.

    Args:
        codegen: The main LLVMCodegen instance.
        stmt: The return statement to emit.

    Raises:
        TypeError: If the return type is not supported.
    """
    # Extract variables being returned (for move semantics)
    moved_vars = _extract_return_variables(stmt.value)

    # Mark returned struct and dynamic array variables as moved (transfer ownership to caller)
    # This prevents RAII cleanup from freeing memory that the caller will receive
    from semantics.typesys import StructType, DynamicArrayType
    for var_name in moved_vars:
        # Check if variable is a struct with dynamic arrays or a dynamic array itself
        semantic_type = codegen.memory.find_semantic_type(var_name)
        if semantic_type:
            if isinstance(semantic_type, StructType):
                if codegen.dynamic_arrays.struct_needs_cleanup(semantic_type):
                    # Mark as moved so RAII cleanup skips it
                    codegen.memory.mark_struct_as_moved(var_name)
            elif isinstance(semantic_type, DynamicArrayType):
                # Mark dynamic array as moved so RAII cleanup skips it
                codegen.dynamic_arrays.mark_as_moved(var_name)

    # Check if we're in an extension method
    if hasattr(codegen, 'in_extension_method') and codegen.in_extension_method:
        # Extension method: return bare value directly
        value = codegen.expressions.emit_expr(stmt.value)
    else:
        # Regular function: emit Result expression (Ok or Err)
        # This will produce a Result struct: {i1 is_ok, T value}
        value = codegen.expressions.emit_expr(stmt.value)

    # RAII: Emit cleanup for all resources using shared utilities
    # This cleans up both struct fields with dynamic arrays and top-level dynamic arrays
    # Note: Moved variables are automatically skipped by the cleanup logic
    from backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    # Return the value (Result struct for functions, bare value for extension methods)
    codegen.builder.ret(value)
