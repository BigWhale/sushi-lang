"""
Array literal emission for fixed-size arrays.

This module handles LLVM IR emission for array literals like [1, 2, 3, 4, 5].
Supports both constant and non-constant element values, creating stack-allocated
arrays as needed.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import ArrayLiteral

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal(codegen: 'LLVMCodegen', expr: ArrayLiteral) -> ir.Value:
    """Emit array literal as LLVM array constant or initialization.

    Creates fixed-size arrays from literal expressions. Supports both constant
    and non-constant element values.

    Args:
        codegen: The LLVM codegen instance.
        expr: The array literal expression.

    Returns:
        An LLVM array value with the literal elements.

    Raises:
        NotImplementedError: If array literal is empty.
    """
    if not expr.elements:
        raise NotImplementedError("empty array literals not supported yet")

    # Emit all element values
    element_values = [codegen.expressions.emit_expr(elem) for elem in expr.elements]

    # Get the element type from the first element
    element_type = element_values[0].type
    array_size = len(element_values)

    # Create LLVM array type
    array_type = ir.ArrayType(element_type, array_size)

    # Fast path: If all elements are constants, create constant array
    if all(isinstance(val, ir.Constant) for val in element_values):
        return ir.Constant(array_type, element_values)

    # Non-constant array: Allocate stack space and store each element
    # Use alloca to create array on stack
    array_alloca = codegen.alloca_builder.alloca(array_type, name="array_literal")

    # Store each element in the array
    zero = ir.Constant(codegen.i32, 0)
    for i, elem_value in enumerate(element_values):
        # Get pointer to array[i] using GEP
        index = ir.Constant(codegen.i32, i)
        elem_ptr = codegen.builder.gep(array_alloca, [zero, index])
        # Store the value
        codegen.builder.store(elem_value, elem_ptr)

    # Load and return the complete array
    return codegen.builder.load(array_alloca)
