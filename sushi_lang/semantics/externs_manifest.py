"""Reserved built-in extern symbols."""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, ForeignPtrType


# Reserved built-in extern symbols and their canonical C-ABI signatures, expressed
# in the FFI type allowlist (BuiltinType / ForeignPtrType). An external whose
# link-name matches one of these but whose signature differs triggers CE5001.
# An identical signature is allowed (LLVM deduplicates declarations).
#
# Each entry maps a C link-name to (param_types_tuple, return_type).
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
