"""Struct field indices for composite types.

This module provides constants for accessing fields in composite structures
like dynamic arrays, enums, strings, and iterators using extract_value/insert_value.
"""

# ============================================================================
# Dynamic Array Struct Indices
# ============================================================================
# Dynamic arrays are represented as: {i32 len, i32 cap, T* data}

DA_LEN_INDEX = 0   # Length field (number of elements currently stored)
DA_CAP_INDEX = 1   # Capacity field (allocated space in elements)
DA_DATA_INDEX = 2  # Data pointer field (pointer to heap-allocated elements)


# ============================================================================
# List<T> Struct Indices
# ============================================================================
# List<T> is represented as: {i32 len, i32 cap, T* data}
# Same layout as dynamic arrays

LIST_LEN_INDEX = 0   # Length field
LIST_CAP_INDEX = 1   # Capacity field
LIST_DATA_INDEX = 2  # Data pointer field


# ============================================================================
# String Struct Indices
# ============================================================================
# String fat pointer: {i8* data, i32 size}

STRING_DATA_INDEX = 0  # Data pointer field (i8*)
STRING_SIZE_INDEX = 1  # Size field (i32)


# ============================================================================
# Enum Struct Indices
# ============================================================================
# Enums are represented as: {i32 tag, [N x i8] data}

ENUM_TAG_INDEX = 0   # Tag/discriminant field (variant identifier)
ENUM_DATA_INDEX = 1  # Data field (variant-specific associated data)


# ============================================================================
# Iterator Struct Indices
# ============================================================================
# Iterator<T>: {i32 current_index, i32 length, T* data_ptr}

ITERATOR_CURRENT_INDEX = 0  # Current index field
ITERATOR_LENGTH_INDEX = 1   # Length field
ITERATOR_DATA_INDEX = 2     # Data pointer field
