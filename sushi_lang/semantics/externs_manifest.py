# semantics/externs_manifest.py
"""
Reserved built-in extern symbols.

Pass 0 needs this manifest to emit CE5001 when a user `unsafe external` block
binds a link-name the compiler already reserves under a different signature.
The declarations themselves are emitted by the backend
(`backend/runtime/core.py::LLVMRuntime.declare_externs`); the two are kept in
sync by `tests/unit/test_ffi.py::test_reserved_externs_are_declared`.
"""
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
