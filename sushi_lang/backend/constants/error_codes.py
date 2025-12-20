"""Error codes and enum tag values.

This module provides constants for:
- Result<T> enum tags
- Maybe<T> enum tags
- Runtime error codes (RExxxx)
"""

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
# Runtime Error Codes
# ============================================================================
# Error codes emitted at runtime via raise_runtime_error()

# RE2020: Array index out of bounds
# Used when accessing array elements with invalid index
RE_ARRAY_INDEX_OUT_OF_BOUNDS = 2020

# RE2021: Memory allocation failure
# Used when malloc/calloc returns null
RE_MEMORY_ALLOCATION_FAILURE = 2021
