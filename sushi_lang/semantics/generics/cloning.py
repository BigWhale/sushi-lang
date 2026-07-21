# semantics/generics/cloning.py
"""
Auto-derived clone() method registration (#134).

Every struct and enum gets a `clone() -> Self` method that deep-copies the
receiver, mirroring the hash auto-derivation pipeline (semantics/generics/hashing.py):
Pass 1.8 registers the BuiltinMethod here so Pass 2 can validate `.clone()` calls,
and the LLVM emitter -- backend code that semantics must not import -- is resolved at
emission time through a factory the backend deposits in sushi_stdlib/src/common.py.

Unlike hash there is no can-be-cloned exclusion: emit_value_clone handles every shape
the destructor handles, including recursive types (via out-of-line emission), so a
plain-data clone is trivially the value itself and every composite is cloneable. The
generic containers Own<T>/List<T>/HashMap<K,V> keep their own method paths and are
excluded here (they are named StructTypes but dispatch through their own machinery).
"""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, StructType, Type
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.common import (
    BuiltinMethod,
    get_builtin_method,
    get_clone_emitter_factory,
    register_builtin_method,
)

# Named StructTypes that own heap through their own registries/method paths; a top-level
# .clone() on these must fall through, not route through the auto-derived struct clone.
_CONTAINER_PREFIXES = ("Own<", "List<", "HashMap<")


def _validate_struct_clone(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate clone() method call on struct types (arity 0, like hash)."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{target_type}.clone", expected=0, got=len(call.args))


def _validate_enum_clone(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate clone() method call on enum types (arity 0, like hash)."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{target_type}.clone", expected=0, got=len(call.args))


def _lazy_clone_emitter(kind: str, target_type: Type):
    """Build a clone() emitter that resolves its backend factory on first emission.

    Deferring the lookup keeps Pass 1.8 free of any backend import and any
    dependency on when the backend's types modules happen to be imported.
    """
    def emit(codegen, call, receiver_value, receiver_type, to_i1):
        factory = get_clone_emitter_factory(kind)
        if factory is None:
            raise_internal_error("CE0127", kind=kind)
        return factory(target_type)(codegen, call, receiver_value, receiver_type, to_i1)

    return emit


def _register_clone_method(target_type: Type, kind: str, validator, description: str) -> None:
    """Register the auto-derived clone() method for a type.

    Called from Pass 1.8 only; a duplicate registration is a no-op guard (the pass
    may see the same type through several tables).
    """
    if get_builtin_method(target_type, "clone") is not None:
        return  # Already registered

    register_builtin_method(
        target_type,
        BuiltinMethod(
            name="clone",
            parameter_types=[],
            return_type=target_type,
            description=description,
            semantic_validator=validator,
            llvm_emitter=_lazy_clone_emitter(kind, target_type),
        )
    )


def register_struct_clone_method(struct_type: StructType) -> None:
    """Register the auto-derived clone() method for a user struct type."""
    if struct_type.name.startswith(_CONTAINER_PREFIXES):
        return  # Own/List/HashMap keep their own method paths
    _register_clone_method(
        struct_type, "struct", _validate_struct_clone,
        f"Auto-derived clone for struct {struct_type}",
    )


def register_enum_clone_method(enum_type: EnumType) -> None:
    """Register the auto-derived clone() method for an enum type."""
    _register_clone_method(
        enum_type, "enum", _validate_enum_clone,
        f"Auto-derived clone for enum {enum_type}",
    )
