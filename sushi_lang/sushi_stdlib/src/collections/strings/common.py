"""Common Utilities for String Operations"""

import llvmlite.ir as ir
from sushi_lang.backend.string_layout import string_size, string_value


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
from sushi_lang.sushi_stdlib.src.string_helpers import emit_checked_malloc


def allocate_and_copy_bytes(
    builder: ir.IRBuilder,
    malloc: ir.Function,
    memcpy: ir.Function,
    src_ptr: ir.Value,
    byte_count: ir.Value,
    i64: ir.IntType
) -> ir.Value:
    """Allocate memory and copy bytes from source."""
    byte_count_i64 = builder.zext(byte_count, i64, name="byte_count_i64")
    new_data = emit_checked_malloc(builder, malloc, byte_count_i64, name="new_data")

    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, src_ptr, builder.zext(byte_count, ir.IntType(64)), is_volatile])

    return new_data


def allocate_substring(
    builder: ir.IRBuilder,
    malloc: ir.Function,
    memcpy: ir.Function,
    src_data: ir.Value,
    start_offset: ir.Value,
    byte_length: ir.Value,
    i32: ir.IntType,
    i64: ir.IntType
) -> ir.Value:
    """Allocate and return a substring as a fat pointer struct."""
    src_ptr = builder.gep(src_data, [start_offset], name="src_ptr")

    new_data = allocate_and_copy_bytes(builder, malloc, memcpy, src_ptr, byte_length, i64)

    return build_string_struct(builder, new_data, byte_length, owned=1)


def build_string_struct(
    builder: ir.IRBuilder,
    data_ptr: ir.Value,
    size: ir.Value,
    owned: int,
    name: str = "result",
) -> ir.Value:
    """Build a string value over `data_ptr` with `size` bytes."""
    return string_value(builder, data_ptr, size, owned, name=name)


def clone_string_to_owned(
    builder: ir.IRBuilder,
    module: ir.Module,
    string_val: ir.Value,
) -> ir.Value:
    """Copy a string fat pointer's bytes into a fresh heap buffer, returning an owned string."""
    malloc = declare_malloc(module)
    memcpy = declare_memcpy(module)
    i64 = ir.IntType(64)

    src_data = builder.extract_value(string_val, 0, name="clone_src_data")
    size = string_size(builder, string_val, name="clone_size")
    size_i64 = builder.zext(size, i64, name="clone_size_i64")
    new_data = emit_checked_malloc(builder, malloc, size_i64, name="clone_data")
    is_volatile = ir.Constant(ir.IntType(1), 0)
    builder.call(memcpy, [new_data, src_data, builder.zext(size, ir.IntType(64)), is_volatile])
    return build_string_struct(builder, new_data, size, owned=1)
