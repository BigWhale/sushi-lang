"""
Backend constants for LLVM code generation.

This module provides centralized constants for:
- LLVM integer type bit widths
- Dynamic array struct field indices
- Result<T> enum tag values
- Maybe<T> enum tag values
- Hash algorithm constants (FNV-1a)

Purpose: Eliminate magic numbers, improve type safety, provide single source of truth.
"""

# ============================================================================
# LLVM Integer Type Bit Widths
# ============================================================================
# Used with ir.IntType(width) throughout the backend

INT8_BIT_WIDTH = 8      # i8 type (bytes, characters)
INT32_BIT_WIDTH = 32    # i32 type (general integers, array lengths)
INT64_BIT_WIDTH = 64    # i64 type (large integers, pointers, hash values)


# ============================================================================
# Dynamic Array Struct Layout
# ============================================================================
# Dynamic arrays are represented as: {i32 len, i32 cap, T* data}
# These indices are used with builder.extract_value() and builder.insert_value()

DA_LEN_INDEX = 0   # Length field (number of elements currently stored)
DA_CAP_INDEX = 1   # Capacity field (allocated space in elements)
DA_DATA_INDEX = 2  # Data pointer field (pointer to heap-allocated elements)


# ============================================================================
# Result<T> Enum Tags
# ============================================================================
# Result<T> is represented as: {i32 tag, [N x i8] data}
# Tag distinguishes between Ok and Err variants

RESULT_OK_TAG = 0   # Result.Ok(value) variant
RESULT_ERR_TAG = 1  # Result.Err() variant


# ============================================================================
# Maybe<T> Enum Tags
# ============================================================================
# Maybe<T> is represented as: {i32 tag, [N x i8] data}
# Tag distinguishes between Some and None variants

MAYBE_SOME_TAG = 0  # Maybe.Some(value) variant
MAYBE_NONE_TAG = 1  # Maybe.None() variant


# ============================================================================
# FNV-1a Hash Algorithm Constants
# ============================================================================
# 64-bit FNV-1a hash constants for consistent hashing across all types
# Algorithm: hash = (hash XOR byte) * FNV_PRIME
# Note: These are also defined in backend/types/hash_utils.py for backward compatibility

FNV1A_OFFSET_BASIS = 14695981039346656037  # 0xcbf29ce484222325 (64-bit)
FNV1A_PRIME = 1099511628211                # 0x100000001b3 (64-bit)
