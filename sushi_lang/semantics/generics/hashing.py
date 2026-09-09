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
    StructType,
    Type,
    UnknownType,
)
from sushi_lang.semantics.generics.cloning import CONTAINER_PREFIXES
from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.sushi_stdlib.src.common import (
    BuiltinMethod,
    get_hash_emitter_factory,
)
from sushi_lang.semantics.generics.type_display import display_type


# A kind a derived hash cannot read, and the phrase that says why. Named rather than
# implied: the three walks each carried their own chain of `isinstance` arms, and a kind
# no chain named fell out of the loop untouched, which reads as hashable. A struct with a
# `fn(i32) -> i32` field then got a hash() the backend could not emit, and the user read
# CE0052 -- an internal error about a program that was theirs to fix (#618).
UNHASHABLE_KINDS: Dict[str, str] = {
    "ForeignPtrType": "a foreign ptr (unhashable)",
    "FunctionType": "a function value (unhashable)",
    "ReferenceType": "a reference (unhashable)",
    "IteratorType": "an iterator (unhashable)",
    "TypeParameter": "an unsubstituted type parameter",
    "TypePack": "a type pack",
    "GenericTypeRef": "an uninstantiated generic type",
    "GenericStructType": "a generic struct (should be monomorphized first)",
    "GenericEnumType": "a generic enum (should be monomorphized first)",
    "PointerType": "a raw pointer (unhashable)",
}

# A kind that holds other types, and answers through the walk over what it holds.
WALKED_KINDS = frozenset({"ArrayType", "DynamicArrayType", "StructType", "EnumType"})

# A kind that is hashable with nothing to walk.
HASHABLE_KINDS = frozenset({"BuiltinType"})

# A kind the walk would let through although the backend cannot hash it. The set is
# EMPTY, and the gate that pins it says so. `PointerType` was the one inhabitant: it
# reaches the walk only inside a container the compiler synthesizes, and letting it
# through gave `List@(T)` and `Own@(T)` a hash the backend could not emit (#628). A
# container now answers from what it HOLDS instead, so the pointer is refused with every
# other unhashable kind and no hole is left open.
LET_THROUGH_KINDS: frozenset[str] = frozenset()

# What a container hashes, and the backend emitter kind that reads it. A container's
# backing FIELDS are the wrong answer: `List@(T).data` and `Own@(T).value` are raw
# pointers, and the hash of an address says nothing about the value. `HashMap@(K, V)` is
# absent on purpose -- its buckets carry a state for each slot and the slot order is not
# the entry order, so it needs a fold that no walk over elements can give -- an emitter
# of its own, and a ruling on which fold. It is refused until then (#628).
CONTAINER_HASH_KINDS: Dict[str, str] = {
    "List": "list",
    "Own": "own",
}


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


def hashability_of(ty: Type, walk: Optional[_Walk] = None) -> tuple[bool, str]:
    """Can a derived hash read a value of `ty`? One reader for every position.

    A struct field, an enum payload and an array element all ask this, so a kind is
    answered once and every position inherits the answer. The dispatch is total over the
    type kinds, and `tests/unit/test_hashability_dispatch_is_total.py` is the gate.
    """
    walk = walk if walk is not None else _Walk()

    if isinstance(ty, UnknownType):
        return False, f"unresolved type '{ty.name}'"

    kind = type(ty).__name__

    if kind in LET_THROUGH_KINDS:
        return True, "let through: see LET_THROUGH_KINDS"

    refusal = UNHASHABLE_KINDS.get(kind)
    if refusal is not None:
        return False, refusal

    if isinstance(ty, (ArrayType, DynamicArrayType)):
        return can_array_be_hashed(ty, walk)

    if isinstance(ty, EnumType):
        return can_enum_be_hashed(ty, walk)

    if isinstance(ty, StructType):
        return can_struct_be_hashed(ty, walk)

    if isinstance(ty, BuiltinType):
        return True, "a primitive"

    # A kind the gate does not know about yet. Refusing loses a hash that might have
    # been derivable; accepting hands the backend a value it cannot read.
    return False, f"unsupported type kind '{kind}'"


def can_struct_be_hashed(struct_type: StructType,
                         walk: Optional[_Walk] = None) -> tuple[bool, str]:
    """Check if a struct type can have an auto-derived hash method."""
    walk = walk if walk is not None else _Walk()
    return _decide(walk, "struct", struct_type.name,
                   lambda: _struct_fields_are_hashable(struct_type, walk))


def container_hash_kind(ty: Type) -> Optional[str]:
    """The emitter kind for a container that hashes what it holds, else None.

    One reader for two questions: whether the walk reads the type arguments instead of
    the fields, and which backend emitter the registration then names.
    """
    base = getattr(ty, "generic_base", None)
    if base is None:
        return None
    return CONTAINER_HASH_KINDS.get(base)


def _container_content_is_hashable(struct_type: StructType, walk: _Walk) -> tuple[bool, str]:
    """A container answers from what it HOLDS, with the walk standing on the container."""
    kind = container_hash_kind(struct_type)
    if kind is None:
        return False, "a container with no derived hash (its slot order is not its entry order)"

    held = struct_type.generic_args or ()
    if not held:
        return False, "a container with no type argument to read"

    for arg in held:
        can_hash, reason = hashability_of(arg, walk)
        if not can_hash:
            return False, f"holds -> {reason}"

    return True, "the type it holds is hashable"


def _struct_fields_are_hashable(struct_type: StructType, walk: _Walk) -> tuple[bool, str]:
    """Every field of one struct, with the walk already standing on that struct."""
    if isinstance(struct_type, GenericStructType):
        return False, UNHASHABLE_KINDS["GenericStructType"]

    # A compiler container's fields are its IMPLEMENTATION, not its content: two of the
    # three hold a raw pointer there. Read what it holds instead (#628).
    if struct_type.name.startswith(CONTAINER_PREFIXES):
        return _container_content_is_hashable(struct_type, walk)

    for field_name, field_type in struct_type.fields:
        can_hash, reason = hashability_of(field_type, walk)
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
    if isinstance(enum_type, GenericEnumType):
        return False, UNHASHABLE_KINDS["GenericEnumType"]

    for variant in enum_type.variants:
        for assoc_type in variant.associated_types:
            can_hash, reason = hashability_of(assoc_type, walk)
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

    can_hash, reason = hashability_of(element_type, walk)
    if not can_hash:
        return False, f"element -> {reason}"

    return True, "element type is hashable"


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


def _register_hash_method(target_type: Type, derived: DerivedMethodTable, kind: str,
                          validator, description: str) -> None:
    """Register the auto-derived hash() method for a type."""
    if derived.get_method(target_type, "hash") is not None:
        return  # Already registered

    derived.register_method(
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


def register_struct_hash_method(struct_type: StructType,
                                derived: DerivedMethodTable) -> None:
    """Register the auto-derived hash() method for a hashable struct type.

    The CALLER decides and this one acts. Every one of the six call sites already asks
    `can_struct_be_hashed` and registers only on a yes, so re-deciding here was the
    second of two exponential walks per type (#598).
    """
    _register_hash_method(
        struct_type, derived, container_hash_kind(struct_type) or "struct",
        _validate_struct_hash,
        f"Auto-derived hash for struct {struct_type}",
    )


def register_enum_hash_method(enum_type: EnumType,
                              derived: DerivedMethodTable) -> None:
    """Register the auto-derived hash() method for a hashable enum type.

    The caller decides; see `register_struct_hash_method`.
    """
    _register_hash_method(
        enum_type, derived, "enum", _validate_enum_hash,
        f"Auto-derived hash for enum {enum_type}",
    )


def register_array_hash_method(array_type: Type,
                               derived: DerivedMethodTable) -> None:
    """Register the auto-derived hash() method for a hashable array type.

    The caller decides; see `register_struct_hash_method`.
    """
    _register_hash_method(
        array_type, derived, "array", _validate_array_hash,
        f"Auto-derived hash for array {array_type}",
    )
