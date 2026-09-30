"""Reserved built-in extern symbols, and the ones the compiler generates."""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType


#: Where each platform keeps `errno` (#1087): a function that answers an `int*`. The
#: `errno()` built-in calls the one for the platform it compiles for.
ERRNO_LOCATION_SYMBOLS: dict[str, str] = {
    "darwin": "__error",
    "linux": "__errno_location",
}


# C link-name -> (param types, return type, var_arg) for the built-in externs that the
# compiler declares itself. Each one is the first declaration of its link name, so a user
# declaration of the name must have the same C types (CE5001, #1099).
RESERVED_EXTERNS: dict[str, tuple] = {
    "strlen":  ((BuiltinType.STRING,), BuiltinType.I64, False),
    "strcmp":  ((BuiltinType.STRING, BuiltinType.STRING), BuiltinType.I32, False),
    "memcmp":  ((ForeignPtrType(), ForeignPtrType(), BuiltinType.I64), BuiltinType.I32, False),
    "sprintf": ((ForeignPtrType(), BuiltinType.STRING), BuiltinType.I32, True),
    "printf":  ((BuiltinType.STRING,), BuiltinType.I32, True),
    "malloc":  ((BuiltinType.I64,), ForeignPtrType(), False),
    "free":    ((ForeignPtrType(),), BuiltinType.BLANK, False),
    "exit":    ((BuiltinType.I32,), BuiltinType.BLANK, False),
    # `errno()` (#1087) declares the platform's location function as a pointer answer.
    **{symbol: ((), ForeignPtrType(), False) for symbol in ERRNO_LOCATION_SYMBOLS.values()},
}


# The generated symbols that live in NO bitcode file: the backend emits them inline
# into the module it compiles, so the stdlib symbol manifest cannot report them.
# Unlike RESERVED_EXTERNS above, these may never be declared at all -- their real
# signature is the compiler's business and an `unsafe external` naming one is CE5013
# (#472). `backend/codegen_llvm.py` reads the same set to give them linkonce_odr.
GENERATED_INLINE_SYMBOLS: frozenset[str] = frozenset({
    "llvm_strlen",
    "llvm_strcmp",
    "utf8_char_count",
})
