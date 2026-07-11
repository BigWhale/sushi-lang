# semantics/generics/primitives.py
"""Recognition and Pass-2 validation of the built-in primitive methods.

Which methods a primitive has, and what a call to one must look like, are
semantic facts. Pass 2 needs them *before* the backend is ever imported -- the
compiler pipeline loads codegen lazily, after semantic analysis -- so they are
decided here and not read out of the builtin-method registry.

The registry still carries the same methods, registered by
`backend/types/primitives/` with their LLVM emitters attached, and the backend
dispatches emission through it. The two are kept in sync by
`tests/unit/test_primitive_methods.py`.

All three methods take no arguments, so validation is uniform; the only per-method
distinction is which types carry them (`to_bits` is float-only).
"""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.internals import errors as er


# Every primitive that carries to_str() and hash().
_ALL_PRIMITIVES = frozenset({
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
})

# to_bits() exposes the raw IEEE-754 encoding, so it exists on floats only.
_FLOAT_PRIMITIVES = frozenset({BuiltinType.F32, BuiltinType.F64})

# Method name -> the primitive types that carry it.
PRIMITIVE_METHOD_TYPES: dict[str, frozenset] = {
    "to_str": _ALL_PRIMITIVES,
    "hash": _ALL_PRIMITIVES,
    "to_bits": _FLOAT_PRIMITIVES,
}


def is_builtin_primitive_method(method_name: str) -> bool:
    """Check if a method name is a builtin primitive method."""
    return method_name in PRIMITIVE_METHOD_TYPES


def has_primitive_method(target_type: Type, method_name: str) -> bool:
    """Check if a primitive type carries the named builtin method.

    A name can be a builtin primitive method in general and still be absent from
    a given type -- `i32.to_bits()` is not a thing -- so callers must ask about
    the receiver, not just the name.
    """
    carriers = PRIMITIVE_METHOD_TYPES.get(method_name)
    return carriers is not None and target_type in carriers


def validate_primitive_method(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate a builtin primitive method call. All three take no arguments."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{target_type}.{call.method}", expected=0, got=len(call.args))
