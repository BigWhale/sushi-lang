"""
Type Definitions

Centralized LLVM type definitions used across stdlib modules.
Eliminates the duplication of inline type definitions throughout the codebase.

Design: Single Source of Truth for all common LLVM types.
"""

import llvmlite.ir as ir
from typing import Tuple


# ==============================================================================
# Basic Types
# ==============================================================================

def get_basic_types() -> Tuple[ir.IntType, ir.PointerType, ir.IntType, ir.IntType]:
    """Get commonly used basic LLVM types.

    Returns:
        Tuple of (i8, i8_ptr, i32, i64)
    """
    i8 = ir.IntType(8)
    i8_ptr = i8.as_pointer()
    i32 = ir.IntType(32)
    i64 = ir.IntType(64)
    return i8, i8_ptr, i32, i64


# ==============================================================================
# String Type (Fat Pointer)
# ==============================================================================

def get_string_type() -> ir.LiteralStructType:
    """Get the string fat pointer type: { i8*, i32 }

    Sushi strings are represented as fat pointers containing:
    - Field 0: i8* data - pointer to string bytes
    - Field 1: i32 size - size in bytes

    Returns:
        String fat pointer struct type
    """
    i8_ptr = ir.IntType(8).as_pointer()
    i32 = ir.IntType(32)
    return ir.LiteralStructType([i8_ptr, i32])


def get_string_types() -> Tuple[ir.IntType, ir.PointerType, ir.IntType, ir.IntType, ir.LiteralStructType]:
    """Get all types commonly needed for string operations.

    This is a convenience function that combines basic types with string type.
    Matches the signature of collections/strings/common.py:get_string_types()

    Returns:
        Tuple of (i8, i8_ptr, i32, i64, string_type)
    """
    i8, i8_ptr, i32, i64 = get_basic_types()
    string_type = get_string_type()
    return i8, i8_ptr, i32, i64, string_type


# ==============================================================================
# Iterator Type
# ==============================================================================

def get_iterator_type(element_type: ir.Type) -> ir.LiteralStructType:
    """Get the iterator struct type for a given element type.

    Iterator structure: { i32 index, i32 length, element_type* data_ptr }
    - Field 0: i32 index - current iteration index
    - Field 1: i32 length - total length (-1 for streaming iterators)
    - Field 2: element_type* data_ptr - pointer to data

    Args:
        element_type: Type of elements being iterated

    Returns:
        Iterator struct type
    """
    i32 = ir.IntType(32)
    element_ptr = element_type.as_pointer()
    return ir.LiteralStructType([i32, i32, element_ptr])


def get_string_iterator_type() -> ir.LiteralStructType:
    """Get the iterator type for iterating over strings.

    This is used for stdin.lines(), file.lines(), and string array iterators.

    Returns:
        Iterator<string> struct type
    """
    string_type = get_string_type()
    return get_iterator_type(string_type)


# ==============================================================================
# Dynamic Array Type
# ==============================================================================

def get_dynamic_array_type(element_type: ir.Type) -> ir.LiteralStructType:
    """Get the dynamic array struct type for a given element type.

    Dynamic array structure: { i32 len, i32 cap, element_type* data }
    - Field 0: i32 len - current length
    - Field 1: i32 cap - allocated capacity
    - Field 2: element_type* data - pointer to data

    Args:
        element_type: Type of array elements

    Returns:
        Dynamic array struct type
    """
    i32 = ir.IntType(32)
    element_ptr = element_type.as_pointer()
    return ir.LiteralStructType([i32, i32, element_ptr])


def get_byte_array_type() -> ir.LiteralStructType:
    """Get the dynamic array type for byte arrays (u8[]).

    Returns:
        u8[] struct type
    """
    i8 = ir.IntType(8)
    return get_dynamic_array_type(i8)


# ==============================================================================
# File Type
# ==============================================================================

def get_file_type() -> ir.PointerType:
    """Get the FILE* type (opaque pointer).

    FILE* is represented as i8* (opaque pointer to FILE struct).

    Returns:
        FILE* type
    """
    return ir.IntType(8).as_pointer()


# ==============================================================================
# Enum Type Helpers
# ==============================================================================

def get_unit_enum_type() -> ir.LiteralStructType:
    """Get the LLVM type for a unit enum (enum with no associated data).

    Unit enums have only discriminant tags, no variant data.
    LLVM layout: {i32 tag, [1 x i8] data}
    The data array has minimum 1 byte even though variants have no data.

    Examples: SeekFrom, FileMode, ProcessError, StdError

    Returns:
        LLVM struct type for unit enums
    """
    i8 = ir.IntType(8)
    i32 = ir.IntType(32)
    return ir.LiteralStructType([i32, ir.ArrayType(i8, 1)])


# ==============================================================================
# Result and Maybe Types (for future use)
# ==============================================================================

def get_result_type(ok_type: ir.Type, err_type: ir.Type = None) -> ir.LiteralStructType:
    """Get the Result<T, E> enum type.

    Result structure: { i32 tag, [N x i8] data }
    - Field 0: i32 tag - discriminant (0=Ok, 1=Err)
    - Field 1: [N x i8] data - byte array containing the packed value

    This matches the actual LLVM layout used by the backend.
    The data field size N is max(sizeof(ok_type), sizeof(err_type), 1).

    Args:
        ok_type: Type of the Ok value
        err_type: Type of the Err value (optional, defaults to calculating from ok_type only)

    Returns:
        Result<T, E> struct type
    """
    from sushi_lang.backend.expressions.memory import calculate_llvm_type_size

    i8 = ir.IntType(8)
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

    data_array = ir.ArrayType(i8, size_bytes)
    return ir.LiteralStructType([i32, data_array])


def get_maybe_type(some_type: ir.Type) -> ir.LiteralStructType:
    """Get the Maybe<T> enum type.

    Maybe structure: { i32 tag, [N x i8] data }
    - Field 0: i32 tag - discriminant (0=Some, 1=None)
    - Field 1: [N x i8] data - byte array containing the packed value

    This matches the actual LLVM layout used by the backend.
    The data field size N is determined by sizeof(some_type).

    Args:
        some_type: Type of the Some value

    Returns:
        Maybe<T> struct type
    """
    from sushi_lang.backend.expressions.memory import calculate_llvm_type_size

    i8 = ir.IntType(8)
    i32 = ir.IntType(32)

    # Calculate size of some_type in bytes using existing infrastructure
    size_bytes = calculate_llvm_type_size(some_type)

    data_array = ir.ArrayType(i8, size_bytes)
    return ir.LiteralStructType([i32, data_array])


# ==============================================================================
# Time Types
# ==============================================================================

def get_timespec_type() -> ir.LiteralStructType:
    """Get the POSIX timespec struct type.

    timespec structure: { i64 tv_sec, i64 tv_nsec }
    - Field 0: i64 tv_sec - seconds
    - Field 1: i64 tv_nsec - nanoseconds [0, 999999999]

    Used by nanosleep() and other POSIX time functions.

    Returns:
        timespec struct type
    """
    i64 = ir.IntType(64)
    return ir.LiteralStructType([i64, i64])


# ==============================================================================
# Legacy Compatibility
# ==============================================================================
# These functions maintain compatibility with existing code.
# New code should use the specific get_*_type() functions above.

def get_types_bundle() -> dict:
    """Get a dictionary of commonly used types.

    This provides a convenient way to access multiple types at once.

    Returns:
        Dictionary mapping type names to LLVM types
    """
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
