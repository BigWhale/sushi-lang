"""Reserved built-in extern symbols, and the ones the compiler generates."""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType


# C link-name -> (param types, return type) for the reserved built-in externs. A matching
# link-name with a DIFFERENT signature is CE5001; an identical one is allowed, since LLVM
# deduplicates declarations.
RESERVED_EXTERNS: dict[str, tuple] = {
    "strlen":  ((BuiltinType.STRING,), BuiltinType.I64),
    "strcmp":  ((ForeignPtrType(), ForeignPtrType()), BuiltinType.I32),
    "memcmp":  ((ForeignPtrType(), ForeignPtrType(), BuiltinType.I64), BuiltinType.I32),
    "sprintf": ((ForeignPtrType(), ForeignPtrType()), BuiltinType.I32),
    "printf":  ((ForeignPtrType(),), BuiltinType.I32),
    "malloc":  ((BuiltinType.I64,), ForeignPtrType()),
    "free":    ((ForeignPtrType(),), BuiltinType.BLANK),
    "exit":    ((BuiltinType.I32,), BuiltinType.BLANK),
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
