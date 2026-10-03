"""The derived contracts `Eq`, `Ord` and `Display`: which types have one, and where.

The compiler derives an equality, an order and a string form for a struct and an enum
from what the type holds, on the model of the derived hash (#696). `extend T with Eq`
(or `Ord`, or `Display`) is the override, and it is terminal: a held type that has one
is not walked.

Two questions, two readers:

- `contract_of` is the HELD rule: can a derived method read a value of this type in a
  field, a payload or an element? A container answers from what it holds: an array and
  a `List@(T)` by their elements, an `Own@(T)` by its payload. A recursive type is
  legal, because the backend emits one function per type and a nested value of the
  same type is a call of it.
- `operand_contract` is the TOP-LEVEL rule, for the operators, the method call and a
  constraint: a primitive keeps its closed set, a struct or an enum that is not a
  container asks the held rule, and everything else is refused. `Maybe` and `Result`
  take `==` and `<`.
- `printed_contract` is the TOP-LEVEL rule of the interpolation hole and `print` /
  `println`: an array, a `List@(T)`, an `Own@(T)` and a `HashMap@(K, V)` ask the held
  rule, so they print in the form a type that holds them prints (#1132), and the map
  is refused with its reason. `Maybe` and `Result` are not printed: a hole and
  `println` make the program handle them first. Everything else is `operand_contract`.

The dispatch is total over the type kinds, and
`tests/unit/test_contract_dispatch_is_total.py` is the gate.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, Dict, Mapping, Optional

from sushi_lang.semantics.generics.contract_walk import Override, Walk, decide
from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.semantics.type_predicates import is_instance_of, is_numeric_type
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, ReferenceType, StructType, Type,
    UnknownType)

if TYPE_CHECKING:
    from sushi_lang.semantics.derived_methods import DerivedMethodTable


EQ = "Eq"
ORD = "Ord"
DISPLAY = "Display"
CONTRACTS = (EQ, ORD, DISPLAY)

#: The method each contract provides, and the perk each method name belongs to.
CONTRACT_METHOD: Mapping[str, str] = MappingProxyType(
    {EQ: "eq", ORD: "compare", DISPLAY: "to_str"})
METHOD_CONTRACT: Mapping[str, str] = MappingProxyType(
    {method: perk for perk, method in CONTRACT_METHOD.items()})

#: The argument count of each contract method; the typecheck pass reads it (CE2009).
CONTRACT_METHOD_ARITY: Mapping[str, int] = MappingProxyType(
    {"eq": 1, "compare": 1, "to_str": 0})

#: What each contract method answers.
CONTRACT_METHOD_RETURN: Mapping[str, BuiltinType] = MappingProxyType(
    {"eq": BuiltinType.BOOL, "compare": BuiltinType.I32, "to_str": BuiltinType.STRING})

# A kind no derived contract can read, and the phrase that says why.
REFUSED_KINDS: Dict[str, str] = {
    "ForeignPtrType": "a foreign ptr",
    "FunctionType": "a function value",
    "ReferenceType": "a reference",
    "IteratorType": "an iterator",
    "TypeParameter": "an unsubstituted type parameter",
    "TypePack": "a type pack",
    "GenericTypeRef": "an uninstantiated generic type",
    "GenericStructType": "a generic struct (should be monomorphized first)",
    "GenericEnumType": "a generic enum (should be monomorphized first)",
    "PointerType": "a raw pointer",
}

# A kind that holds other types, and answers through the walk over what it holds.
WALKED_KINDS = frozenset({"ArrayType", "DynamicArrayType", "StructType", "EnumType"})

# A kind every contract accepts with nothing to walk.
ACCEPTED_KINDS = frozenset({"BuiltinType"})

# The containers that answer from what they HOLD. `HashMap@(K, V)` is refused: its
# slot order is not its entry order, so no walk over its buckets gives an equality, an
# order or a string form that two equal maps share.
HELD_CONTAINERS = ("List", "Own")
_HASHMAP_REFUSAL = "a HashMap (its slot order is not its entry order)"

# The primitive sets at the top level, which are the operators' closed sets. A bool
# has no order at the top level (`a < b` on two bools is a typo for `!=`); a bool held
# in a type orders false before true.
_TOP_EQ_NON_NUMERIC = frozenset({BuiltinType.BOOL, BuiltinType.STRING})
_TOP_ORD_NON_NUMERIC = frozenset({BuiltinType.STRING})
_TOP_DISPLAY_NON_NUMERIC = frozenset({BuiltinType.BOOL, BuiltinType.STRING})


def contract_of(ty: Type, contract: str, walk: Optional[Walk] = None, *,
                resolve: Optional[Callable[[Type], Type]] = None,
                overridden: Optional[Override] = None) -> tuple[bool, str]:
    """Can the derived `contract` read a value of `ty` where it is HELD?

    `overridden` is asked first: an implementation of the contract wins in every
    position and is terminal, so a type that has one is accepted whatever it holds.
    """
    walk = walk if walk is not None else Walk(resolve=resolve, overridden=overridden,
                                              cycle_answer=True)
    if walk.resolve is not None:
        ty = walk.resolve(ty)

    if walk.overridden is not None and walk.overridden(ty):
        return True, f"a {contract} implementation (the override)"

    if isinstance(ty, UnknownType):
        return False, f"unresolved type '{ty.name}'"

    kind = type(ty).__name__
    refusal = REFUSED_KINDS.get(kind)
    if refusal is not None:
        return False, refusal

    if isinstance(ty, (ArrayType, DynamicArrayType)):
        answer, reason = contract_of(ty.base_type, contract, walk)
        return (True, "the element is") if answer else (False, f"element -> {reason}")

    if isinstance(ty, EnumType):
        return decide(walk, "enum", ty.name, lambda: _enum_payloads(ty, contract, walk))

    if isinstance(ty, StructType):
        return decide(walk, "struct", ty.name, lambda: _struct_fields(ty, contract, walk))

    if isinstance(ty, BuiltinType):
        return True, "a primitive"

    return False, f"unsupported type kind '{kind}'"


def _struct_fields(struct_type: StructType, contract: str, walk: Walk) -> tuple[bool, str]:
    """Every field of one struct; a container reads what it holds instead."""
    if isinstance(struct_type, GenericStructType):
        return False, REFUSED_KINDS["GenericStructType"]
    if is_instance_of(struct_type, "HashMap"):
        return False, _HASHMAP_REFUSAL
    if is_instance_of(struct_type, *HELD_CONTAINERS):
        held = struct_type.generic_args or ()
        if not held:
            return False, "a container with no type argument to read"
        for arg in held:
            answer, reason = contract_of(arg, contract, walk)
            if not answer:
                return False, f"holds -> {reason}"
        return True, "what it holds is"
    for field_name, field_type in struct_type.fields:
        answer, reason = contract_of(field_type, contract, walk)
        if not answer:
            return False, f"field '{field_name}' -> {reason}"
    return True, "every field is"


def _enum_payloads(enum_type: EnumType, contract: str, walk: Walk) -> tuple[bool, str]:
    """Every payload of one enum."""
    if isinstance(enum_type, GenericEnumType):
        return False, REFUSED_KINDS["GenericEnumType"]
    for variant in enum_type.variants:
        for assoc_type in variant.associated_types:
            answer, reason = contract_of(assoc_type, contract, walk)
            if not answer:
                return False, f"variant {variant.name} -> {reason}"
    return True, "every payload is"


def _top_level_primitive(ty: BuiltinType, contract: str) -> bool:
    if is_numeric_type(ty):
        return True
    if contract == EQ:
        return ty in _TOP_EQ_NON_NUMERIC
    if contract == ORD:
        return ty in _TOP_ORD_NON_NUMERIC
    return ty in _TOP_DISPLAY_NON_NUMERIC


def is_contract_receiver(ty: Type) -> bool:
    """A struct or an enum that is not a compiler container: the one kind that DERIVES."""
    from sushi_lang.semantics.generics.cloning import CONTAINER_BASES
    return (isinstance(ty, (StructType, EnumType))
            and not is_instance_of(ty, *CONTAINER_BASES))


def operand_contract(ty: Optional[Type], contract: str, *,
                     overridden: Optional[Override] = None,
                     resolve: Optional[Callable[[Type], Type]] = None
                     ) -> tuple[bool, Optional[str]]:
    """The TOP-LEVEL rule: may a value of `ty` meet `==`, `<`, a hole or `println`?

    The answer carries the reason a derived contract cannot read the type, or None
    when there is nothing to add to the refusal (a kind the top level never takes).
    """
    if ty is None:
        return False, None
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if resolve is not None:
        ty = resolve(ty)
    if isinstance(ty, BuiltinType):
        return _top_level_primitive(ty, contract), None
    if not is_contract_receiver(ty):
        return False, None
    if contract == DISPLAY and is_instance_of(ty, "Maybe", "Result"):
        return False, None
    return contract_of(ty, contract, resolve=resolve, overridden=overridden)


def printed_contract(ty: Optional[Type], *,
                     overridden: Optional[Override] = None,
                     resolve: Optional[Callable[[Type], Type]] = None
                     ) -> tuple[bool, Optional[str]]:
    """The TOP-LEVEL rule of a hole and `print`/`println`: may a value of `ty` print?"""
    if ty is None:
        return False, None
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if resolve is not None:
        ty = resolve(ty)
    if (isinstance(ty, (ArrayType, DynamicArrayType))
            or is_instance_of(ty, "HashMap", *HELD_CONTAINERS)):
        return contract_of(ty, DISPLAY, resolve=resolve, overridden=overridden)
    return operand_contract(ty, DISPLAY, overridden=overridden, resolve=resolve)


def override_of(derived: 'DerivedMethodTable', contract: str) -> Optional[Override]:
    """The compilation's override predicate for one contract, if the table has one."""
    overrides = getattr(derived, "overrides", None)
    return overrides.get(contract) if overrides else None
