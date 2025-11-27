"""
Array and struct initialization helpers for the Sushi language compiler.

This module provides helper functions for initializing arrays (both fixed and dynamic)
with proper element-by-element initialization and memory management.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from internals.errors import raise_internal_error
from backend.utils import require_builder

if TYPE_CHECKING:
    from llvmlite import ir
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import ArrayLiteral, DynamicArrayNew, DynamicArrayFrom
    from semantics.typesys import DynamicArrayType


def initialize_array_literal(
    codegen: 'LLVMCodegen',
    slot: 'ir.AllocaInstr',
    array_literal: 'ArrayLiteral',
    array_type: 'ir.ArrayType'
) -> None:
    """Initialize array variable with array literal elements.

    Creates GEP instructions for each array element and stores the
    evaluated expression value at that location.

    Args:
        codegen: The main LLVMCodegen instance.
        slot: The alloca instruction for the array variable.
        array_literal: The array literal AST node.
        array_type: The LLVM array type.
    """
    from llvmlite import ir
    builder = require_builder(codegen)
    # Initialize each element of the array
    for i, element_expr in enumerate(array_literal.elements):
        # Emit the element expression
        element_value = codegen.expressions.emit_expr(element_expr)

        # Create GEP to the array element: array[0][i]
        zero = ir.Constant(codegen.i32, 0)
        index = ir.Constant(codegen.i32, i)
        gep = codegen.builder.gep(slot, [zero, index])

        # Store the element value
        # Cast the element if needed
        casted_element = codegen.utils.cast_for_param(element_value, array_type.element)
        codegen.builder.store(casted_element, gep)


def initialize_dynamic_array(
    codegen: 'LLVMCodegen',
    name: str,
    array_type: 'DynamicArrayType',
    constructor_expr
) -> None:
    """Initialize dynamic array variable with constructor or expression.

    Handles three cases:
    1. `new()` constructors (empty arrays) - optimized path
    2. `from([...])` constructors (arrays with initial elements) - optimized path
    3. General expressions returning arrays (function calls, ??, etc.) - direct initialization

    Args:
        codegen: The main LLVMCodegen instance.
        name: The variable name.
        array_type: The DynamicArrayType.
        constructor_expr: The initialization expression (DynamicArrayNew, DynamicArrayFrom, or any Expr).

    Raises:
        TypeError: If the constructor expression type is not supported.
    """
    from semantics.ast import DynamicArrayNew, DynamicArrayFrom
    from llvmlite import ir
    if codegen.dynamic_arrays is None:
        raise_internal_error("CE0014")

    # First, declare the dynamic array in the memory manager - this creates the alloca
    alloca = codegen.dynamic_arrays.declare_dynamic_array(name, array_type)

    # Register the alloca with the regular memory manager for name resolution
    current_scope_level = len(codegen.memory.locals) - 1
    codegen.memory.locals[-1][name] = alloca

    # Update flat cache for O(1) lookup
    if name not in codegen.memory._flat_locals_cache:
        codegen.memory._flat_locals_cache[name] = []
    codegen.memory._flat_locals_cache[name].append((current_scope_level, alloca))

    # Also register semantic type for method dispatch (e.g., .iter())
    codegen.memory.semantic_types[-1][name] = array_type

    # Update flat cache for semantic types
    if name not in codegen.memory._flat_types_cache:
        codegen.memory._flat_types_cache[name] = []
    codegen.memory._flat_types_cache[name].append((current_scope_level, array_type))

    # Then initialize based on constructor type
    if isinstance(constructor_expr, DynamicArrayNew):
        # Optimized path: empty array with new()
        codegen.dynamic_arrays.emit_array_constructor_new(name)
    elif isinstance(constructor_expr, DynamicArrayFrom):
        # Optimized path: array literal with from([...])
        elements = []
        for element_expr in constructor_expr.elements.elements:
            element_value = codegen.expressions.emit_expr(element_expr)
            elements.append(element_value)
        codegen.dynamic_arrays.emit_array_constructor_from(name, elements)
    else:
        # General case: any expression returning a dynamic array
        # This handles function calls, method calls, ??, etc.
        val = codegen.expressions.emit_expr(constructor_expr)

        # If val is a pointer to a dynamic array struct (from stack-allocated returns),
        # load the struct value
        if isinstance(val.type, ir.PointerType) and codegen.types.is_dynamic_array_type(val.type.pointee):
            val = codegen.builder.load(val, name=f"{name}_init_value")

        # Store the struct value into the alloca
        codegen.builder.store(val, alloca)
