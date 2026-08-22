"""Recognition and Pass-2 validation of the built-in primitive methods."""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType, Type
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type


_ALL_PRIMITIVES = frozenset({
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING,
})

# The primitives whose clone() this module owns. `string` is absent, and the split from
# the string method table STAYS: a string clone deep-copies a heap buffer while every
# other primitive's clone is the identity, so one name would cover two mechanisms. A
# STRING row here would also be unreachable, because the typecheck pass consults the string table
# first. The rule is one SEAM, not one table -- `builtin_method_exists` ORs both families,
# and tests/unit/test_clone_totality.py asks only through it.
_CLONE_PRIMITIVES = _ALL_PRIMITIVES - {BuiltinType.STRING}

# method name -> {receiver type: return type}. Keyed per (method, RECEIVER) because
# to_bits() is receiver-dependent -- f32 yields u32, f64 yields u64 -- and the width is
# the whole point. Sole authority for both questions; PRIMITIVE_METHOD_TYPES is a derived
# view, so the two cannot drift.
PRIMITIVE_METHOD_RETURNS: dict[str, dict[BuiltinType, BuiltinType]] = {
    "to_str": dict.fromkeys(sorted(_ALL_PRIMITIVES, key=str), BuiltinType.STRING),
    "hash": dict.fromkeys(sorted(_ALL_PRIMITIVES, key=str), BuiltinType.U64),
    "to_bits": {BuiltinType.F32: BuiltinType.U32, BuiltinType.F64: BuiltinType.U64},
    # The identity: a primitive owns no heap. It must still EXIST, because one
    # monomorphized body has to satisfy `T = i32` and `T = string` alike.
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
    """Check if a primitive type carries the named builtin method."""
    carriers = PRIMITIVE_METHOD_TYPES.get(method_name)
    return carriers is not None and target_type in carriers


def primitive_method_return_type(target_type: Type, method_name: str) -> Type | None:
    """Return type of a builtin primitive method call, or None if this pair has none."""
    return PRIMITIVE_METHOD_RETURNS.get(method_name, {}).get(target_type)


def validate_primitive_method(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate a builtin primitive method call. All three take no arguments."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.{call.method}", expected=0, got=len(call.args))
