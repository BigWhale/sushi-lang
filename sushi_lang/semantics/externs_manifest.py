"""Reserved built-in extern symbols."""
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
