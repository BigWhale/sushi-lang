"""
Array indexing operations with bounds checking.

This module handles LLVM IR emission for array element access (array[index]).
Includes runtime bounds checking with error RE2020 for out-of-bounds access.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from semantics.ast import IndexAccess, Name
from internals.errors import raise_internal_error
from backend.utils import require_builder

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_index_access(codegen: 'LLVMCodegen', expr: IndexAccess, to_i1: bool = False) -> ir.Value:
    """Emit array indexing operation using GEP instruction.

    Performs array element access with runtime bounds checking for fixed arrays.
    Emits runtime error RE2020 if index is out of bounds.

    Args:
        codegen: The LLVM codegen instance.
        expr: The index access expression.
        to_i1: Whether to convert result to i1 for boolean contexts.

    Returns:
        The value at the specified array index.

    Note:
        Emits runtime error RE2020 for out-of-bounds access on fixed arrays.
    """
    builder = require_builder(codegen)
    # For array indexing, we need to get the array slot directly from the variable
    # rather than loading the array value
    if isinstance(expr.array, Name):
        # Get the array slot directly from memory manager
        array_slot = codegen.memory.find_local_slot(expr.array.id)
    else:
        # For more complex array expressions, emit normally
        array_value = codegen.expressions.emit_expr(expr.array)
        array_slot = array_value

    # Emit the index expression (should be an integer)
    index_value = codegen.expressions.emit_expr(expr.index)

    # Compile-time constant checking: detect negative or out-of-bounds constant indices
    if isinstance(index_value, ir.Constant):
        const_index = index_value.constant
        # Check for negative index
        if const_index < 0:
            raise_internal_error("CE2056", index=const_index)
        # For fixed arrays, check if index is out of bounds at compile time
        array_type = array_slot.type.pointee
        if isinstance(array_type, ir.ArrayType):
            array_size = array_type.count
            if const_index >= array_size:
                raise_internal_error("CE2057", index=const_index, size=array_size)

    # Add runtime bounds checking for fixed arrays
    # Get the array type and size
    array_type = array_slot.type.pointee
    if isinstance(array_type, ir.ArrayType):
        # Fixed array - add bounds checking
        array_size = array_type.count
        size_const = ir.Constant(codegen.i32, array_size)
        zero = ir.Constant(codegen.i32, 0)

        # Check if index >= 0
        index_not_negative = codegen.builder.icmp_signed(">=", index_value, zero, name="index_not_negative")

        # Check if index < size
        index_in_bounds = codegen.builder.icmp_unsigned("<", index_value, size_const, name="index_in_bounds")

        # Both conditions must be true
        bounds_ok = codegen.builder.and_(index_not_negative, index_in_bounds, name="bounds_ok")

        # Create basic blocks for bounds check
        bounds_ok_block = codegen.builder.append_basic_block(name="array_bounds_ok")
        bounds_fail_block = codegen.builder.append_basic_block(name="array_bounds_fail")

        # Branch based on bounds check
        codegen.builder.cbranch(bounds_ok, bounds_ok_block, bounds_fail_block)

        # Bounds failure block: emit runtime error and exit
        codegen.builder.position_at_end(bounds_fail_block)
        codegen.runtime.errors.emit_runtime_error_with_values(
            "RE2020",
            "array index %d out of bounds for array of size %d",
            index_value,
            size_const
        )
        # emit_runtime_error_with_values calls exit(), so this block is terminated
        # Add unreachable to satisfy LLVM
        codegen.builder.unreachable()

        # Bounds OK block: continue with normal array access
        codegen.builder.position_at_end(bounds_ok_block)

    # Use GEP to get pointer to the array element
    # llvmlite's GEP validation requires constant indices for structs and arrays
    # Workaround: Convert to element pointer first, then use single-index GEP
    from backend import gep_utils

    zero = ir.Constant(codegen.i32, 0)

    if isinstance(array_type, ir.ArrayType):
        # Fixed array: Get pointer to first element, then use pointer arithmetic
        # This avoids llvmlite's .constant validation for the second index
        first_elem_ptr = codegen.builder.gep(array_slot, [zero, zero], name="first_elem")
        element_ptr = gep_utils.gep_array_element(codegen, first_elem_ptr, index_value, "elem_ptr")
    elif isinstance(array_type, ir.LiteralStructType):
        # Dynamic array struct: Extract data pointer, then use pointer arithmetic
        # This avoids llvmlite's .constant validation for struct field indices
        data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, array_slot, "data_ptr")
        data_ptr = codegen.builder.load(data_ptr_ptr, name="array_data")
        element_ptr = gep_utils.gep_array_element(codegen, data_ptr, index_value, "elem_ptr")
    else:
        # Other pointer types (shouldn't happen for array indexing)
        element_ptr = codegen.builder.gep(array_slot, [zero, index_value])

    # Load the value from the pointer
    result = codegen.builder.load(element_ptr)

    return codegen.utils.as_i1(result) if to_i1 else result
