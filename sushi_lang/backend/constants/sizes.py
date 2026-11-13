"""Type and struct size constants."""

from sushi_lang.backend.string_layout import STRING_BYTES

# String value: {i8* data, i64 size, i8 owned}. It is the aligned sizeof, not the raw 17,
# so a string round-tripped through an enum/Result/Maybe payload keeps the owned byte
# (#145). `backend/string_layout.py` is its one definition.
FAT_POINTER_SIZE_BYTES = STRING_BYTES

# Closure/function-value fat pointer:
# {i8* fn_ptr, i8* env_ptr, i8* drop_ptr, i8* clone_ptr} = 4 * 8 = 32 bytes.
# Distinct from the string fat pointer above.
CLOSURE_FAT_POINTER_SIZE_BYTES = 32

DYNAMIC_ARRAY_SIZE_BYTES = 16

# An iterator value has the closure's four words: {next_fn, state, drop_fn, clone_fn}.
ITERATOR_SIZE_BYTES = CLOSURE_FAT_POINTER_SIZE_BYTES

# Enum payload base offset: the i32 tag plus 4 bytes of struct padding before the
# [K x i64] data member (#300 phase 2). The payload therefore starts 8-aligned, which
# is what lets every payload field access use natural alignment (the align=1 family
# was retired with this change).
ENUM_TAG_SIZE_BYTES = 8
