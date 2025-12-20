"""Backend constants facade.

This module re-exports all constants from the backend/constants/ subdirectory,
maintaining backward compatibility with the old backend/constants.py API.

Organized by category:
- bit_widths: LLVM integer type bit widths
- llvm_values: Precomputed LLVM constant values and factory functions
- sizes: Primitive and composite type sizes
- indices: Struct field indices for composite types
- error_codes: Result/Maybe tags and runtime error codes
- hash_constants: FNV-1a hash algorithm constants
"""

# Bit widths
from sushi_lang.backend.constants.bit_widths import (
    INT8_BIT_WIDTH,
    INT16_BIT_WIDTH,
    INT32_BIT_WIDTH,
    INT64_BIT_WIDTH,
)

# LLVM constant values and factory functions
from sushi_lang.backend.constants.llvm_values import (
    FALSE_I1,
    TRUE_I1,
    ZERO_I8,
    ONE_I8,
    ZERO_I16,
    ONE_I16,
    ZERO_I32,
    ONE_I32,
    TWO_I32,
    ZERO_I64,
    ONE_I64,
    make_i8_const,
    make_i16_const,
    make_i32_const,
    make_i64_const,
    make_bool_const,
    make_int_const,
    gep_indices_struct,
    LIST_LEN_INDICES,
    LIST_CAP_INDICES,
    LIST_DATA_INDICES,
)

# Type and struct sizes
from sushi_lang.backend.constants.sizes import (
    I8_SIZE_BYTES,
    I16_SIZE_BYTES,
    I32_SIZE_BYTES,
    I64_SIZE_BYTES,
    U8_SIZE_BYTES,
    U16_SIZE_BYTES,
    U32_SIZE_BYTES,
    U64_SIZE_BYTES,
    F32_SIZE_BYTES,
    F64_SIZE_BYTES,
    BOOL_SIZE_BYTES,
    POINTER_SIZE_BYTES,
    FAT_POINTER_SIZE_BYTES,
    DYNAMIC_ARRAY_SIZE_BYTES,
    ITERATOR_SIZE_BYTES,
    ENUM_TAG_SIZE_BYTES,
)

# Struct field indices
from sushi_lang.backend.constants.indices import (
    DA_LEN_INDEX,
    DA_CAP_INDEX,
    DA_DATA_INDEX,
    LIST_LEN_INDEX,
    LIST_CAP_INDEX,
    LIST_DATA_INDEX,
    STRING_DATA_INDEX,
    STRING_SIZE_INDEX,
    ENUM_TAG_INDEX,
    ENUM_DATA_INDEX,
    ITERATOR_CURRENT_INDEX,
    ITERATOR_LENGTH_INDEX,
    ITERATOR_DATA_INDEX,
)

# Error codes and enum tags
from sushi_lang.backend.constants.error_codes import (
    RESULT_OK_TAG,
    RESULT_ERR_TAG,
    MAYBE_SOME_TAG,
    MAYBE_NONE_TAG,
    RE_ARRAY_INDEX_OUT_OF_BOUNDS,
    RE_MEMORY_ALLOCATION_FAILURE,
)

# Hash algorithm constants
from sushi_lang.backend.constants.hash_constants import (
    FNV1A_OFFSET_BASIS,
    FNV1A_PRIME,
)

__all__ = [
    # Bit widths
    'INT8_BIT_WIDTH',
    'INT16_BIT_WIDTH',
    'INT32_BIT_WIDTH',
    'INT64_BIT_WIDTH',
    # LLVM values
    'FALSE_I1',
    'TRUE_I1',
    'ZERO_I8',
    'ONE_I8',
    'ZERO_I16',
    'ONE_I16',
    'ZERO_I32',
    'ONE_I32',
    'TWO_I32',
    'ZERO_I64',
    'ONE_I64',
    'make_i8_const',
    'make_i16_const',
    'make_i32_const',
    'make_i64_const',
    'make_bool_const',
    'make_int_const',
    'gep_indices_struct',
    'LIST_LEN_INDICES',
    'LIST_CAP_INDICES',
    'LIST_DATA_INDICES',
    # Sizes
    'I8_SIZE_BYTES',
    'I16_SIZE_BYTES',
    'I32_SIZE_BYTES',
    'I64_SIZE_BYTES',
    'U8_SIZE_BYTES',
    'U16_SIZE_BYTES',
    'U32_SIZE_BYTES',
    'U64_SIZE_BYTES',
    'F32_SIZE_BYTES',
    'F64_SIZE_BYTES',
    'BOOL_SIZE_BYTES',
    'POINTER_SIZE_BYTES',
    'FAT_POINTER_SIZE_BYTES',
    'DYNAMIC_ARRAY_SIZE_BYTES',
    'ITERATOR_SIZE_BYTES',
    'ENUM_TAG_SIZE_BYTES',
    # Indices
    'DA_LEN_INDEX',
    'DA_CAP_INDEX',
    'DA_DATA_INDEX',
    'LIST_LEN_INDEX',
    'LIST_CAP_INDEX',
    'LIST_DATA_INDEX',
    'STRING_DATA_INDEX',
    'STRING_SIZE_INDEX',
    'ENUM_TAG_INDEX',
    'ENUM_DATA_INDEX',
    'ITERATOR_CURRENT_INDEX',
    'ITERATOR_LENGTH_INDEX',
    'ITERATOR_DATA_INDEX',
    # Error codes
    'RESULT_OK_TAG',
    'RESULT_ERR_TAG',
    'MAYBE_SOME_TAG',
    'MAYBE_NONE_TAG',
    'RE_ARRAY_INDEX_OUT_OF_BOUNDS',
    'RE_MEMORY_ALLOCATION_FAILURE',
    # Hash constants
    'FNV1A_OFFSET_BASIS',
    'FNV1A_PRIME',
]
