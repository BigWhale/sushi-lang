"""Type Definitions"""

import llvmlite.ir as ir
from typing import Tuple


# ==============================================================================
# Basic Types
# ==============================================================================

def get_basic_types() -> Tuple[ir.IntType, ir.PointerType, ir.IntType, ir.IntType]:
    """Get commonly used basic LLVM types."""
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    return i8, i8_ptr, i32, i64


# ==============================================================================
# String Type (Fat Pointer)
# ==============================================================================

def get_string_type() -> ir.LiteralStructType:
    """The string fat pointer `{i8* data, i32 size, i8 owned}`.

    `owned` is a runtime discriminator: 1 = heap (RAII frees), 0 = literal or borrow (never
    freed). LLVM sizeof stays 16, so this is byte-compatible with the old `{i8*, i32}`
    wherever a string embeds. Must stay in lockstep with backend
    mapping.py:_create_string_struct_type. See docs/design/string-representation.md.
    """
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    return ir.LiteralStructType([i8_ptr, i32, i8])


def get_string_types() -> Tuple[ir.IntType, ir.PointerType, ir.IntType, ir.IntType, ir.LiteralStructType]:
    """Get all types commonly needed for string operations."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    return i8, i8_ptr, i32, i64, string_type


# ==============================================================================
# Iterator Type
# ==============================================================================

def get_iterator_type(element_type: ir.Type) -> ir.LiteralStructType:
    """The iterator struct `{i32 index, i32 length, T* data}`; length -1 means streaming."""
    i32 = ir.IntType(32)
    element_ptr = element_type.as_pointer()
    return ir.LiteralStructType([i32, i32, element_ptr])


def get_string_iterator_type() -> ir.LiteralStructType:
    """Get the iterator type for iterating over strings."""
    string_type = get_string_type()
    return get_iterator_type(string_type)


# ==============================================================================
# Dynamic Array Type
# ==============================================================================

def get_dynamic_array_type(element_type: ir.Type) -> ir.LiteralStructType:
    """Get the dynamic array struct type for a given element type."""
    i32 = ir.IntType(32)
    element_ptr = element_type.as_pointer()
    return ir.LiteralStructType([i32, i32, element_ptr])


def get_byte_array_type() -> ir.LiteralStructType:
    """Get the dynamic array type for byte arrays (u8[])."""
    i8 = ir.IntType(8)
    return get_dynamic_array_type(i8)


# ==============================================================================
# File Type
# ==============================================================================

def get_file_type() -> ir.PointerType:
    """Get the FILE* type (opaque pointer)."""
    return ir.IntType(8).as_pointer()


# ==============================================================================
# ProcessOutput Struct
# ==============================================================================

def get_process_output_type() -> ir.LiteralStructType:
    """Get the ProcessOutput struct VALUE type: { i32 exit_code, string, string }."""
    i32 = ir.IntType(32)
    string_type = get_string_type()
    return ir.LiteralStructType([i32, string_type, string_type])


def _process_output_size_bytes() -> int:
    """Aligned size of ProcessOutput, mirroring backend sizing.py `_calculate_struct_size`."""
    string_size = 16  # fat pointer {i8*, i32, i8 owned} aligned sizeof (backend FAT_POINTER_SIZE_BYTES) (#145)
    fields = [(4, 4), (string_size, 8), (string_size, 8)]
    offset = 0
    max_align = 1
    for size, align in fields:
        max_align = max(max_align, align)
        if offset % align:
            offset += align - (offset % align)
        if size % align:
            size += align - (size % align)
        offset += size
    if offset % max_align:
        offset += max_align - (offset % max_align)
    return offset


def get_process_output_result_type() -> ir.LiteralStructType:
    """Result<ProcessOutput, ProcessError> LLVM layout: { i32 tag, [5 x i64] data }."""
    i32 = ir.IntType(32)
    data_bytes = max(_process_output_size_bytes(), 1)
    return ir.LiteralStructType([i32, ir.ArrayType(ir.IntType(64), _payload_word_count(data_bytes))])


# ==============================================================================
# Enum Type Helpers
# ==============================================================================

def _payload_word_count(byte_size: int) -> int:
    """i64 words needed for `byte_size` payload bytes, minimum 1 (#300 phase 2)."""
    return max((byte_size + 7) // 8, 1)


def get_unit_enum_type() -> ir.LiteralStructType:
    """Get the LLVM type for a unit enum (enum with no associated data)."""
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    return ir.LiteralStructType([i32, ir.ArrayType(i64, 1)])


# ==============================================================================
# Result and Maybe Types (for future use)
# ==============================================================================

def get_result_type(ok_type: ir.Type, err_type: ir.Type = None) -> ir.LiteralStructType:
    """Get the Result<T, E> enum type."""
    from sushi_lang.backend.expressions.memory import calculate_llvm_type_size

    i32 = ir.IntType(32)

    # Calculate size of ok_type in bytes
    ok_size = calculate_llvm_type_size(ok_type)

    # Calculate size of err_type if provided
    if err_type is not None:
        err_size = calculate_llvm_type_size(err_type)
        # Use the maximum of ok_type and err_type sizes, with minimum 1 byte
        size_bytes = max(ok_size, err_size, 1)
    else:
        # Legacy behavior: use only ok_type size
        size_bytes = max(ok_size, 1)

    data_array = ir.ArrayType(ir.IntType(64), _payload_word_count(size_bytes))
    return ir.LiteralStructType([i32, data_array])


def get_maybe_type(some_type: ir.Type) -> ir.LiteralStructType:
    """Get the Maybe<T> enum type."""
    from sushi_lang.backend.expressions.memory import calculate_llvm_type_size

    i32 = ir.IntType(32)

    # Calculate size of some_type in bytes using existing infrastructure
    size_bytes = calculate_llvm_type_size(some_type)

    data_array = ir.ArrayType(ir.IntType(64), _payload_word_count(max(size_bytes, 1)))
    return ir.LiteralStructType([i32, data_array])


# ==============================================================================
# Time Types
# ==============================================================================

def get_timespec_type() -> ir.LiteralStructType:
    """Get the POSIX timespec struct type."""
    i64 = ir.IntType(64)
    return ir.LiteralStructType([i64, i64])


# ==============================================================================
# Legacy Compatibility
# ==============================================================================
# These functions maintain compatibility with existing code.
# New code should use the specific get_*_type() functions above.

def get_types_bundle() -> dict:
    """Get a dictionary of commonly used types."""
    i8, i8_ptr, i32, i64 = get_basic_types()
    return {
        'i8': i8,
        'i8_ptr': i8_ptr,
        'i32': i32,
        'i64': i64,
        'string': get_string_type(),
        'file': get_file_type(),
        'string_iterator': get_string_iterator_type(),
        'byte_array': get_byte_array_type(),
    }
