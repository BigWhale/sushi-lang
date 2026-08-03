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

Every one of them takes no arguments, so validation is uniform; the only per-method
distinction is which types carry them (`to_bits` is float-only, `clone` excludes
`string`).
"""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type


# Every primitive that carries to_str() and hash().
_ALL_PRIMITIVES = frozenset({
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
})

# The primitives whose clone() this module owns. `string` is absent on purpose: it carries
# its own clone in the string method table, which Pass 2 consults BEFORE the primitive
# path, so registering it here would be a second answer to one question.
_CLONE_PRIMITIVES = _ALL_PRIMITIVES - {BuiltinType.STRING}

# method name -> {receiver type: return type}.
#
# Keyed per (method, RECEIVER) because to_bits() is receiver-dependent: it exposes the raw
# IEEE-754 encoding, so f32 yields u32 and f64 yields u64. A method-keyed table could not
# express that, and the width is the whole point of the method -- `let u32 b = f64val.to_bits()`
# used to compile and silently truncate the pattern to 32 bits.
#
# This is the sole authority for both questions ("which types carry it" and "what does it
# return"); PRIMITIVE_METHOD_TYPES below is a derived view, so the two cannot drift.
PRIMITIVE_METHOD_RETURNS: dict[str, dict[BuiltinType, BuiltinType]] = {
    "to_str": dict.fromkeys(sorted(_ALL_PRIMITIVES, key=str), BuiltinType.STRING),
    "hash": dict.fromkeys(sorted(_ALL_PRIMITIVES, key=str), BuiltinType.U64),
    "to_bits": {BuiltinType.F32: BuiltinType.U32, BuiltinType.F64: BuiltinType.U64},
    # clone() returns the receiver's own type, so this row is the identity. A primitive
    # owns no heap, so the copy is the value itself -- but the method must EXIST, because
    # one monomorphized body has to satisfy both `T = i32` and `T = string` and only the
    # string instantiation needs a deep copy. Rust makes `Copy: Clone` for this reason.
    "clone": {t: t for t in sorted(_CLONE_PRIMITIVES, key=str)},
}

# Method name -> the primitive types that carry it. Derived; do not edit independently.
PRIMITIVE_METHOD_TYPES: dict[str, frozenset] = {
    name: frozenset(returns) for name, returns in PRIMITIVE_METHOD_RETURNS.items()
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


def primitive_method_return_type(target_type: Type, method_name: str) -> Type | None:
    """Return type of a builtin primitive method call, or None if this pair has none.

    Pass 2 must answer this WITHOUT the builtin-method registry. The registry carries the
    same methods, but it is populated by `backend/types/primitives/` at import time and the
    pipeline imports codegen lazily, AFTER semantic analysis -- so during Pass 2 it is empty.
    Reading it from here is what left every primitive return type un-inferred (#239): a
    missing inference is silent, because validate_assignment_compatibility treats an
    unknown value type as "nothing to check".
    """
    return PRIMITIVE_METHOD_RETURNS.get(method_name, {}).get(target_type)


def validate_primitive_method(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate a builtin primitive method call. All three take no arguments."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.{call.method}", expected=0, got=len(call.args))
