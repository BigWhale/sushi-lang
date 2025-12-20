"""LLVM integer type bit widths.

This module provides centralized constants for LLVM IR integer type bit widths.
Used with ir.IntType(width) throughout the backend.
"""

# Integer type bit widths
INT8_BIT_WIDTH = 8      # i8 type (bytes, characters)
INT16_BIT_WIDTH = 16    # i16 type
INT32_BIT_WIDTH = 32    # i32 type (general integers, array lengths)
INT64_BIT_WIDTH = 64    # i64 type (large integers, pointers, hash values)
