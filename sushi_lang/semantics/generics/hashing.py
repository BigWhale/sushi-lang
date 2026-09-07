"""Hashability analysis and hash() method registration."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    ForeignPtrType,
    StructType,
    Type,
    UnknownType,
)
from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.common import (
    BuiltinMethod,
    get_builtin_method,
    get_hash_emitter_factory,
    register_builtin_method,
)
from sushi_lang.semantics.generics.type_display import display_type


@dataclass
class _Walk:
    """One hashability walk: where it is, and what it has already decided.

    `on_path` is the DFS path and nothing else. It is what detects RECURSION, and it
    has to be the path: a type reached twice through two different fields is not
    recursive, and the copied-per-field set the walk used to carry could not tell the
    two apart without multiplying the work by the fan-out at every link (#598).

    `decided` is the memo that makes the walk linear. It holds only an answer no cycle
    took part in -- a "recursive" verdict is true of the PATH that found it and of
    nothing else, so remembering one would refuse a type that a different reader can
    hash. `cycles` counts the hits, and an unchanged count over a subtree is what says
    its answer is the type's own.
    """

    path: List[str] = field(default_factory=list)
    on_path: Set[str] = field(default_factory=set)
    decided: Dict[Tuple[str, str], Tuple[bool, str]] = field(default_factory=dict)
    cycles: int = 0


@contextmanager
def _walking(walk: _Walk, name: str) -> Iterator[None]:
    """`name` is on the path for the length of this block, and off it after."""
    walk.on_path.add(name)
    walk.path.append(name)
    try:
        yield
    finally:
        walk.path.pop()
        walk.on_path.discard(name)


def _decide(walk: _Walk, kind: str, name: str,
            verdict: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """One NAMED type's answer, walked at most once per walk.

    Type identity is nominal, so one name is one type and one answer serves every field
    that reaches it.
    """
    key = (kind, name)
    remembered = walk.decided.get(key)
    if remembered is not None:
        return remembered

    if name in walk.on_path:
        walk.cycles += 1
        return False, f"recursive {kind} type: {' -> '.join(walk.path + [name])}"

    hits = walk.cycles
    with _walking(walk, name):
        answer = verdict()
    if walk.cycles == hits:
        walk.decided[key] = answer
    return answer


def can_struct_be_hashed(struct_type: StructType,
                         walk: Optional[_Walk] = None) -> tuple[bool, str]:
    """Check if a struct type can have an auto-derived hash method."""
    walk = walk if walk is not None else _Walk()
    return _decide(walk, "struct", struct_type.name,
                   lambda: _struct_fields_are_hashable(struct_type, walk))


def _struct_fields_are_hashable(struct_type: StructType, walk: _Walk) -> tuple[bool, str]:
    """Every field of one struct, with the walk already standing on that struct."""
    # Generic structs cannot be hashed (should be monomorphized first)
    if isinstance(struct_type, GenericStructType):
        return False, f"generic struct {struct_type.name} (should be monomorphized first)"

    for field_name, field_type in struct_type.fields:
        if isinstance(field_type, UnknownType):
            return False, f"field '{field_name}' has unresolved type '{field_type.name}'"

        if isinstance(field_type, ForeignPtrType):
            return False, f"field '{field_name}' is a foreign ptr (unhashable)"

        if isinstance(field_type, (ArrayType, DynamicArrayType)):
            can_hash, reason = can_array_be_hashed(field_type, walk)
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        if isinstance(field_type, EnumType):
            can_hash, reason = can_enum_be_hashed(field_type, walk)
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        if isinstance(field_type, StructType):
            can_hash, reason = can_struct_be_hashed(field_type, walk)
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

    return True, "all fields are hashable"


def can_enum_be_hashed(enum_type: EnumType,
                       walk: Optional[_Walk] = None) -> tuple[bool, str]:
    """Check if an enum type can have an auto-derived hash method."""
    walk = walk if walk is not None else _Walk()
    return _decide(walk, "enum", enum_type.name,
                   lambda: _enum_payloads_are_hashable(enum_type, walk))


def _enum_payloads_are_hashable(enum_type: EnumType, walk: _Walk) -> tuple[bool, str]:
    """Every payload of one enum, with the walk already standing on that enum."""
    # Generic enums cannot be hashed (should be monomorphized first)
    if isinstance(enum_type, GenericEnumType):
        return False, f"generic enum {enum_type.name} (should be monomorphized first)"

    for variant in enum_type.variants:
        for assoc_type in variant.associated_types:
            if isinstance(assoc_type, UnknownType):
                return False, f"variant {variant.name} has unresolved type '{assoc_type.name}'"

            if isinstance(assoc_type, ForeignPtrType):
                return False, f"variant {variant.name} carries a foreign ptr (unhashable)"

            if isinstance(assoc_type, (ArrayType, DynamicArrayType)):
                can_hash, reason = can_array_be_hashed(assoc_type, walk)
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            if isinstance(assoc_type, EnumType):
                can_hash, reason = can_enum_be_hashed(assoc_type, walk)
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            if isinstance(assoc_type, StructType):
                can_hash, reason = can_struct_be_hashed(assoc_type, walk)
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

    return True, "all variant types are hashable"


def can_array_be_hashed(array_type: Type,
                        walk: Optional[_Walk] = None) -> tuple[bool, str]:
    """Check if an array type can have an auto-derived hash method.

    An array has no name of its own, so there is nothing here to memoize or to stand
    on: it is its ELEMENT that answers, and the walk is only carried through.
    """
    if not isinstance(array_type, (ArrayType, DynamicArrayType)):
        return False, f"not an array type: {type(array_type).__name__}"

    walk = walk if walk is not None else _Walk()
    element_type = array_type.base_type

    if isinstance(element_type, (ArrayType, DynamicArrayType)):
        return False, "nested array type (arrays of arrays not supported)"

    if isinstance(element_type, BuiltinType):
        return True, "element type is primitive"

    if isinstance(element_type, StructType):
        can_hash, reason = can_struct_be_hashed(element_type, walk)
        if not can_hash:
            return False, f"element struct type cannot be hashed: {reason}"
        return True, "element struct type is hashable"

    if isinstance(element_type, EnumType):
        can_hash, reason = can_enum_be_hashed(element_type, walk)
        if not can_hash:
            return False, f"element enum type cannot be hashed: {reason}"
        return True, "element enum type is hashable"

    return False, f"element type {element_type} is not hashable"


def _validate_struct_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on struct types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))


def _validate_enum_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on enum types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))


def _validate_array_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on array types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))

    if isinstance(target_type, (ArrayType, DynamicArrayType)):
        element_type = target_type.base_type
        if isinstance(element_type, (ArrayType, DynamicArrayType)):
            er.emit(reporter, er.ERR.CE2051, call.loc,
                    message="cannot hash array of arrays (nested arrays not supported)")


def _lazy_hash_emitter(kind: str, target_type: Type):
    """Build a hash() emitter that resolves its backend factory on first emission."""
    def emit(codegen, call, receiver_value, receiver_type, to_i1):
        factory = get_hash_emitter_factory(kind)
        if factory is None:
            raise_internal_error("CE0123", kind=kind)
        return factory(target_type)(codegen, call, receiver_value, receiver_type, to_i1)

    return emit


def _register_hash_method(target_type: Type, kind: str, validator, description: str) -> None:
    """Register the auto-derived hash() method for a type."""
    if get_builtin_method(target_type, "hash") is not None:
        return  # Already registered

    register_builtin_method(
        target_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=description,
            semantic_validator=validator,
            llvm_emitter=_lazy_hash_emitter(kind, target_type),
        )
    )


def register_struct_hash_method(struct_type: StructType) -> None:
    """Register the auto-derived hash() method for a hashable struct type.

    The CALLER decides and this one acts. Every one of the six call sites already asks
    `can_struct_be_hashed` and registers only on a yes, so re-deciding here was the
    second of two exponential walks per type (#598).
    """
    _register_hash_method(
        struct_type, "struct", _validate_struct_hash,
        f"Auto-derived hash for struct {struct_type}",
    )


def register_enum_hash_method(enum_type: EnumType) -> None:
    """Register the auto-derived hash() method for a hashable enum type.

    The caller decides; see `register_struct_hash_method`.
    """
    _register_hash_method(
        enum_type, "enum", _validate_enum_hash,
        f"Auto-derived hash for enum {enum_type}",
    )


def register_array_hash_method(array_type: Type) -> None:
    """Register the auto-derived hash() method for a hashable array type.

    The caller decides; see `register_struct_hash_method`.
    """
    _register_hash_method(
        array_type, "array", _validate_array_hash,
        f"Auto-derived hash for array {array_type}",
    )
