"""Type and struct size constants."""


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


POINTER_SIZE_BYTES = 8       # 64-bit pointers (i8*, T*)


# String fat pointer: {i8* data, i32 size, i8 owned} -- aligned LLVM sizeof = 16 bytes
# (data@0..8, size@8..12, owned@12, pad@13..16). MUST be the aligned sizeof, not the raw
# 13, so a string round-tripped through an enum/Result/Maybe payload preserves the owned
# byte at offset 12 (#145). calculate_llvm_type_size() special-cases the string to 16 too.
FAT_POINTER_SIZE_BYTES = 16

# Closure/function-value fat pointer:
# {i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr} = 4 * 8 = 32 bytes.
# Distinct from the string fat pointer above.
CLOSURE_FAT_POINTER_SIZE_BYTES = 32

DYNAMIC_ARRAY_SIZE_BYTES = 16

ITERATOR_SIZE_BYTES = 16

# Enum payload base offset: the i32 tag plus 4 bytes of struct padding before the
# [K x i64] data member (#300 phase 2). The payload therefore starts 8-aligned, which
# is what lets every payload field access use natural alignment (the align=1 family
# was retired with this change).
ENUM_TAG_SIZE_BYTES = 8
