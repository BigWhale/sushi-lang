"""
Common Utilities for String Operations

Shared helper functions to reduce code duplication across string method implementations.
Follows DRY and SOLID principles by extracting repeated patterns into reusable components.

REFACTORED: Type definitions have been moved to stdlib.src.type_definitions
"""

import llvmlite.ir as ir
from typing import Tuple, Callable
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_types


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
#   from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc, declare_memcpy
#
# For backward compatibility during transition, re-export from libc_declarations:
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_malloc, declare_memcpy


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

    # Build and return struct (freshly malloc'd substring -> heap-owned)
    return build_string_struct(builder, string_type, new_data, byte_length, owned=1)


def build_string_struct(
    builder: ir.IRBuilder,
    string_type: ir.LiteralStructType,
    data_ptr: ir.Value,
    size: ir.Value,
    owned: int,
) -> ir.Value:
    """Build a string fat pointer struct { i8*, i32, i8 owned }.

    `owned` is REQUIRED (no default) so every construction site declares heap-vs-literal
    explicitly (issue #145) -- an un-updated caller is a loud TypeError rather than a
    silent owned=undef that corrupts the RAII free path.

    Args:
        builder: IR builder
        string_type: String struct type
        data_ptr: Data pointer (i8*)
        size: Size in bytes (i32)
        owned: 1 if this is a fresh heap allocation the runtime must free, 0 if it is a
               global-backed literal / borrow that must never be freed.

    Returns:
        String struct value
    """
    owned_flag = ir.Constant(ir.IntType(8), 1 if owned else 0)
    undef_struct = ir.Constant(string_type, ir.Undefined)
    struct_with_data = builder.insert_value(undef_struct, data_ptr, 0, name="struct_with_data")
    struct_with_size = builder.insert_value(struct_with_data, size, 1, name="struct_with_size")
    struct_complete = builder.insert_value(struct_with_size, owned_flag, 2, name="result")
    return struct_complete


def clone_string_to_owned(
    builder: ir.IRBuilder,
    module: ir.Module,
    string_val: ir.Value,
    string_type: ir.LiteralStructType,
) -> ir.Value:
    """Copy a string fat pointer's bytes into a fresh heap buffer, returning an owned string.

    Used by string-method "no change" fast-paths that would otherwise return the input
    fat pointer aliased (`ret func.args[0]`). Aliasing the input is unsound under string
    RAII (issue #145): if the caller binds the result while the input still lives, both
    would carry owned=1 over the SAME buffer and double-free. Cloning keeps the invariant
    "every string method returns a fresh, independently owned string."

    Args:
        builder: IR builder positioned at the return site.
        module: LLVM module (to declare malloc/memcpy).
        string_val: The source string fat pointer to copy.
        string_type: String struct type.

    Returns:
        A new string fat pointer {copy, size, owned=1}.
    """
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)

    src_data = builder.extract_value(string_val, 0, name="clone_src_data")
    size = builder.extract_value(string_val, 1, name="clone_size")
    size_i64 = builder.zext(size, i64, name="clone_size_i64")
    new_data = builder.call(malloc, [size_i64], name="clone_data")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, src_data, size, is_volatile])
    return build_string_struct(builder, string_type, new_data, size, owned=1)


# ==============================================================================
# Character Transformation Helpers
# ==============================================================================
# NOTE: This functionality has been moved to stdlib.src.ir_builders.IRLoopBuilder
# The emit_char_transform_loop function is now deprecated. Use:
#   IRLoopBuilder.build_char_transform_loop(...)
# instead.
