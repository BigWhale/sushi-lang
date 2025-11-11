"""
Helper utilities for dynamic array operations.

This module provides shared utility functions for creating and initializing
dynamic array structures, used by both constructors and struct field initialization.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def create_dynamic_array_from_elements(codegen: 'LLVMCodegen', element_type, element_llvm_type: ir.Type,
                                       elements: list[ir.Value]) -> ir.Value:
    """Create a dynamic array struct value from a list of elements.

    Allocates memory with power-of-2 capacity and initializes with provided elements.
    Used by struct constructors and from() constructor.

    Args:
        codegen: The LLVM codegen instance.
        element_type: The Sushi language type of elements.
        element_llvm_type: The LLVM type of elements.
        elements: List of LLVM values for the array elements.

    Returns:
        An LLVM struct value representing the dynamic array.

    Note:
        May emit runtime error RE2021 if realloc fails.
    """
    from backend.expressions import memory

    # Calculate capacity (next power of 2)
    initial_len = len(elements)
    if initial_len == 0:
        # Empty array
        zero_i32 = ir.Constant(codegen.types.i32, 0)
        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
        array_struct_type = ir.LiteralStructType([
            codegen.types.i32,
            codegen.types.i32,
            ir.PointerType(element_llvm_type)
        ])
        array_struct = ir.Constant(array_struct_type, ir.Undefined)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 0)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 1)
        array_struct = codegen.builder.insert_value(array_struct, null_ptr, 2)
        return array_struct

    # Calculate capacity (power of 2)
    capacity = 1
    while capacity < initial_len:
        capacity *= 2

    # Allocate memory
    # Use centralized size calculation with semantic type
    element_size = codegen.types.get_type_size_constant(element_type)
    capacity_val = ir.Constant(codegen.types.i32, capacity)
    total_bytes = codegen.builder.mul(capacity_val, element_size, name="total_bytes")

    # Allocate memory using realloc with null pointer (acts as malloc)
    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    data_ptr = memory.emit_realloc_call(codegen, null_ptr, total_bytes)

    # Cast void* to element_type*
    typed_data_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type))

    # Copy elements to allocated memory
    for i, element_value in enumerate(elements):
        element_ptr = codegen.builder.gep(typed_data_ptr, [ir.Constant(codegen.types.i32, i)])
        codegen.builder.store(element_value, element_ptr)

    # Create the array struct
    array_struct_type = ir.LiteralStructType([
        codegen.types.i32,
        codegen.types.i32,
        ir.PointerType(element_llvm_type)
    ])

    len_val = ir.Constant(codegen.types.i32, initial_len)
    cap_val = ir.Constant(codegen.types.i32, capacity)

    array_struct = ir.Constant(array_struct_type, ir.Undefined)
    array_struct = codegen.builder.insert_value(array_struct, len_val, 0)
    array_struct = codegen.builder.insert_value(array_struct, cap_val, 1)
    array_struct = codegen.builder.insert_value(array_struct, typed_data_ptr, 2)

    return array_struct
