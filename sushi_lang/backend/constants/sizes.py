"""Type and struct size constants.

This module provides constants for primitive type sizes and composite structure sizes.
All sizes are in bytes for 64-bit x86-64 architecture.
"""

# ============================================================================
# Primitive Type Sizes (bytes)
# ============================================================================

I8_SIZE_BYTES = 1
I16_SIZE_BYTES = 2
I32_SIZE_BYTES = 4
I64_SIZE_BYTES = 8

U8_SIZE_BYTES = 1
U16_SIZE_BYTES = 2
U32_SIZE_BYTES = 4
U64_SIZE_BYTES = 8

F32_SIZE_BYTES = 4
F64_SIZE_BYTES = 8

BOOL_SIZE_BYTES = 1

# ============================================================================
# Pointer Sizes (64-bit architecture)
# ============================================================================

POINTER_SIZE_BYTES = 8       # 64-bit pointers (i8*, T*)


# ============================================================================
# Composite Structure Sizes (bytes)
# ============================================================================

# String fat pointer: {i8* data, i32 size, i8 owned} -- aligned LLVM sizeof = 16 bytes
# (data@0..8, size@8..12, owned@12, pad@13..16). MUST be the aligned sizeof, not the raw
# 13, so a string round-tripped through an enum/Result/Maybe payload preserves the owned
# byte at offset 12 (#145). calculate_llvm_type_size() special-cases the string to 16 too.
FAT_POINTER_SIZE_BYTES = 16

# Closure/function-value fat pointer: {i8* fn_ptr, i8* env_ptr, i8* drop_ptr}
# = 8 + 8 + 8 = 24 bytes. Distinct from the string fat pointer above.
CLOSURE_FAT_POINTER_SIZE_BYTES = 24

# Dynamic array struct: {i32 len, i32 cap, T* data} = 4 + 4 + 8 = 16 bytes
DYNAMIC_ARRAY_SIZE_BYTES = 16

# Iterator struct: {i32 current_index, i32 length, T* data_ptr} = 4 + 4 + 8 = 16 bytes
ITERATOR_SIZE_BYTES = 16

# Enum tag size (discriminant field)
ENUM_TAG_SIZE_BYTES = 4
