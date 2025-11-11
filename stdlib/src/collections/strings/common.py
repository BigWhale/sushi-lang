"""
Common Utilities for String Operations

Shared helper functions to reduce code duplication across string method implementations.
Follows DRY and SOLID principles by extracting repeated patterns into reusable components.

REFACTORED: Type definitions have been moved to stdlib.src.type_definitions
"""

import llvmlite.ir as ir
from typing import Tuple, Callable
from stdlib.src.type_definitions import get_string_types


# ==============================================================================
# Type Definitions
# ==============================================================================
# NOTE: get_string_types() is now imported from type_definitions.py
# This eliminates one of the 117+ duplicate type definitions across the codebase


# ==============================================================================
# External Function Declarations
# ==============================================================================
# NOTE: These have been moved to stdlib.src.libc_declarations
# Import them from there instead:
#   from stdlib.src.libc_declarations import declare_malloc, declare_memcpy
#
# For backward compatibility during transition, re-export from libc_declarations:
from stdlib.src.libc_declarations import declare_malloc, declare_memcpy


# ==============================================================================
# Memory Allocation Helpers
# ==============================================================================

def allocate_and_copy_bytes(
    builder: ir.IRBuilder,
    malloc: ir.Function,
    memcpy: ir.Function,
    src_ptr: ir.Value,
    byte_count: ir.Value,
    i64: ir.IntType
) -> ir.Value:
    """Allocate memory and copy bytes from source.

    Args:
        builder: IR builder
        malloc: malloc function
        memcpy: memcpy function
        src_ptr: Source pointer (i8*)
        byte_count: Number of bytes to copy (i32)
        i64: i64 type for malloc argument

    Returns:
        Pointer to allocated and copied data (i8*)
    """
    # Allocate memory
    byte_count_i64 = builder.zext(byte_count, i64, name="byte_count_i64")
    new_data = builder.call(malloc, [byte_count_i64], name="new_data")

    # Copy bytes using llvm.memcpy intrinsic
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, src_ptr, byte_count, is_volatile])

    return new_data


def allocate_substring(
    builder: ir.IRBuilder,
    malloc: ir.Function,
    memcpy: ir.Function,
    string_type: ir.LiteralStructType,
    src_data: ir.Value,
    start_offset: ir.Value,
    byte_length: ir.Value,
    i32: ir.IntType,
    i64: ir.IntType
) -> ir.Value:
    """Allocate and return a substring as a fat pointer struct.

    This is a common pattern in slice operations: calculate offset, allocate,
    copy bytes, and build the struct.

    Args:
        builder: IR builder
        malloc: malloc function
        memcpy: memcpy function
        string_type: String fat pointer type
        src_data: Source string data pointer (i8*)
        start_offset: Byte offset to start from (i32)
        byte_length: Number of bytes to copy (i32)
        i32, i64: LLVM types

    Returns:
        String struct value
    """
    # Calculate source pointer
    src_ptr = builder.gep(src_data, [start_offset], name="src_ptr")

    # Allocate and copy
    new_data = allocate_and_copy_bytes(builder, malloc, memcpy, src_ptr, byte_length, i64)

    # Build and return struct
    return build_string_struct(builder, string_type, new_data, byte_length)


def build_string_struct(
    builder: ir.IRBuilder,
    string_type: ir.LiteralStructType,
    data_ptr: ir.Value,
    size: ir.Value
) -> ir.Value:
    """Build a string fat pointer struct { i8*, i32 }.

    Args:
        builder: IR builder
        string_type: String struct type
        data_ptr: Data pointer (i8*)
        size: Size in bytes (i32)

    Returns:
        String struct value
    """
    undef_struct = ir.Constant(string_type, ir.Undefined)
    struct_with_data = builder.insert_value(undef_struct, data_ptr, 0, name="struct_with_data")
    struct_complete = builder.insert_value(struct_with_data, size, 1, name="result")
    return struct_complete


# ==============================================================================
# Character Transformation Helpers
# ==============================================================================
# NOTE: This functionality has been moved to stdlib.src.ir_builders.IRLoopBuilder
# The emit_char_transform_loop function is now deprecated. Use:
#   IRLoopBuilder.build_char_transform_loop(...)
# instead.
