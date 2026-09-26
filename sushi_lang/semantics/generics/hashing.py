"""Hashability analysis and hash() method registration."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    StructType,
    Type,
    UnknownType,
)
from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.builtin_methods import derived_method
from sushi_lang.sushi_stdlib.src.common import get_hash_emitter_factory


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

#: "Does this type carry a `Hashable` implementation?" -- the override, read at every
#: held position of the walk.
HashOverride = Callable[[Type], bool]


def hash_override_of(perk_impls: Any, generic_perk_impls: Any = None) -> HashOverride:
    """The one override predicate, over the perk-implementation tables (#891).

    An explicit implementation answers, and so does a generic-target template whose
    copy for this instantiation is not cut yet. The tables fill in place, so the
    predicate reads them when it is asked and not when it is built.
    """
    from sushi_lang.semantics.passes.collect.perks import PerkCollector, _get_type_name
    hashable = PerkCollector.HASHABLE_PERK

    def overridden(ty: Type) -> bool:
        type_name = _get_type_name(ty)
        if type_name is not None and perk_impls.implements(type_name, hashable):
            return True
        base = getattr(ty, "generic_base", None)
        args = getattr(ty, "generic_args", None)
        if not generic_perk_impls or not base or not args:
            return False
        return any(template.impl.perk_name == hashable
                   and len(template.type_params) == len(args)
                   for template in generic_perk_impls.templates(base))

    return overridden


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

    `resolve` maps a WRITTEN name to its table entry, for a reader that runs before the
    resolve pass: the constraint check in monomorphize asks about a struct whose
    fields still spell their types (#696). The derive pass runs after that pass and
    hands over none.

    `overridden` is the `Hashable` override (#891): a held type that implements it is
    terminal and hashable, and its fields are not read.
    """

    path: List[str] = field(default_factory=list)
    on_path: Set[str] = field(default_factory=set)
    decided: Dict[Tuple[str, str], Tuple[bool, str]] = field(default_factory=dict)
    cycles: int = 0
    resolve: Optional[Callable[[Type], Type]] = None
    overridden: Optional[HashOverride] = None


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


def hashability_of(ty: Type, walk: Optional[_Walk] = None, *,
                   resolve: Optional[Callable[[Type], Type]] = None,
                   overridden: Optional[HashOverride] = None) -> tuple[bool, str]:
    """Can a derived hash read a value of `ty`? One reader for every position.

    A struct field, an enum payload and an array element all ask this, so a kind is
    answered once and every position inherits the answer. The dispatch is total over the
    type kinds, and `tests/unit/test_hashability_dispatch_is_total.py` is the gate.

    `resolve` is for a reader that runs BEFORE the resolve pass -- the `Hashable`
    constraint check (#696): it maps a written name to its table entry at every step
    of the walk, so a field that spells `Point` is read as the struct it names.

    `overridden` is asked first: a `Hashable` implementation wins in every position
    and is terminal, so a type that has one is hashable whatever its fields hold.
    """
    walk = walk if walk is not None else _Walk(resolve=resolve, overridden=overridden)
    if walk.resolve is not None:
        ty = walk.resolve(ty)

    if walk.overridden is not None and walk.overridden(ty):
        return True, "a Hashable implementation (the override)"

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


def can_struct_be_hashed(struct_type: StructType, walk: Optional[_Walk] = None, *,
                         overridden: Optional[HashOverride] = None) -> tuple[bool, str]:
    """Check if a struct type can have an auto-derived hash method."""
    walk = walk if walk is not None else _Walk(overridden=overridden)
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
    if is_instance_of(struct_type, *CONTAINER_BASES):
        return _container_content_is_hashable(struct_type, walk)

    for field_name, field_type in struct_type.fields:
        can_hash, reason = hashability_of(field_type, walk)
        if not can_hash:
            return False, f"field '{field_name}' -> {reason}"

    return True, "all fields are hashable"


def can_enum_be_hashed(enum_type: EnumType, walk: Optional[_Walk] = None, *,
                       overridden: Optional[HashOverride] = None) -> tuple[bool, str]:
    """Check if an enum type can have an auto-derived hash method."""
    walk = walk if walk is not None else _Walk(overridden=overridden)
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


def can_array_be_hashed(array_type: Type, walk: Optional[_Walk] = None, *,
                        overridden: Optional[HashOverride] = None) -> tuple[bool, str]:
    """Check if an array type can have an auto-derived hash method.

    An array has no name of its own, so there is nothing here to memoize or to stand
    on: it is its ELEMENT that answers, and the walk is only carried through.
    """
    if not isinstance(array_type, (ArrayType, DynamicArrayType)):
        return False, f"not an array type: {type(array_type).__name__}"

    walk = walk if walk is not None else _Walk(overridden=overridden)
    element_type = array_type.base_type

    if isinstance(element_type, (ArrayType, DynamicArrayType)):
        return False, "nested array type (arrays of arrays not supported)"

    can_hash, reason = hashability_of(element_type, walk)
    if not can_hash:
        return False, f"element -> {reason}"

    return True, "element type is hashable"


#: The derived `hash` takes no argument; the typecheck pass reads this row (CE2009).
DERIVED_HASH_ARITY: Mapping[str, int] = MappingProxyType({"hash": 0})


def register_hash_if_hashable(target_type: Type, derived: DerivedMethodTable) -> bool:
    """Register the derived hash() for a struct, an enum or an array that can have one.

    The one "decide, then register" step: the gate for the type's kind decides, and a
    yes registers through the one derived-method seam. Answers whether the type now has
    a derived hash. Any other kind answers False and registers nothing.
    """
    overridden = derived.hash_override
    if isinstance(target_type, (ArrayType, DynamicArrayType)):
        can_hash, _ = can_array_be_hashed(target_type, overridden=overridden)
        kind = "array"
    elif isinstance(target_type, EnumType):
        can_hash, _ = can_enum_be_hashed(target_type, overridden=overridden)
        kind = "enum"
    elif isinstance(target_type, StructType):
        can_hash, _ = can_struct_be_hashed(target_type, overridden=overridden)
        kind = container_hash_kind(target_type) or "struct"
    else:
        return False
    if not can_hash:
        return False
    if derived.get_method(target_type, "hash") is None:
        derived.register_method(target_type, derived_method(
            target_type, name="hash", kind=kind, return_type=BuiltinType.U64,
            factory_getter=get_hash_emitter_factory, ice=er.ERR.CE0123))
    if kind in CONTAINER_HASH_KINDS.values():
        _hash_held_arrays(target_type, derived)
    return True


def _hash_held_arrays(container: StructType, derived: DerivedMethodTable) -> None:
    """A container's held ARRAY argument gets its hash with the container's.

    No struct field and no enum payload names that array, so the derive pass never
    meets it, and the backend's registration on demand has no override predicate to
    read: an array of an overridden type was refused there (CE0052, #891).
    """
    for arg in container.generic_args or ():
        if isinstance(arg, (ArrayType, DynamicArrayType)):
            register_hash_if_hashable(arg, derived)
