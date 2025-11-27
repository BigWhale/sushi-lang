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

# String fat pointer: {i8* data, i32 size} = 8 + 4 = 12 bytes
FAT_POINTER_SIZE_BYTES = 12

# Dynamic array struct: {i32 len, i32 cap, T* data} = 4 + 4 + 8 = 16 bytes
DYNAMIC_ARRAY_SIZE_BYTES = 16

# Iterator struct: {i32 current_index, i32 length, T* data_ptr} = 4 + 4 + 8 = 16 bytes
ITERATOR_SIZE_BYTES = 16

# Enum tag size (discriminant field)
ENUM_TAG_SIZE_BYTES = 4
