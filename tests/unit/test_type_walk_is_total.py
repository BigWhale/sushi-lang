"""`walk_named_types` must reach every part of every type. No silent skips.

Three predicates used to recurse over a type independently -- `contains_foreign_ptr`,
`contains_reference` and `validate_type_name` -- and each had a different hole. A hole in
a type walk is not a missing feature: `peek Nope` fell through `validate_type_name` and
reached the backend as a CE0020 "this is a bug in the Sushi compiler", and a `fn(ptr) i32`
in a `public fn` signature was invisible to CE5008. One walk, one gate.
"""
from __future__ import annotations

import typing

from sushi_lang.semantics import typesys
from sushi_lang.semantics.type_walk import TERMINAL_KINDS, walk_named_types
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    ForeignPtrType, FunctionType, IteratorType, PointerType, ReferenceType,
    StructType, UnknownType,
)
from sushi_lang.semantics.generics.types import (
    GenericEnumType, GenericStructType, GenericTypeRef, TypeParameter, TypePack,
)


def _type_union_members() -> set[str]:
    """Every kind in the `Type` union (semantics/typesys.py)."""
    return {t.__name__ for t in typing.get_args(typesys.Type)}


# The kinds that live in the symbol tables but are not in the `Type` union: a generic
# TEMPLATE is not a type, and a pack is a binding. The walk still has to enter them,
# because a private type can hide in a template's field or a pack's member.
OFF_UNION_KINDS = {"GenericStructType", "GenericEnumType", "TypePack"}


def _marker() -> ForeignPtrType:
    """A terminal the walk cannot invent, so finding it proves the walk arrived."""
    return ForeignPtrType()


def _every_composite() -> dict[str, object]:
    """One value per composite kind, each hiding the marker exactly one level down."""
    m = _marker()
    return {
        "ArrayType": ArrayType(base_type=m, size=3),
        "DynamicArrayType": DynamicArrayType(base_type=m),
        "IteratorType": IteratorType(element_type=m),
        "ReferenceType": ReferenceType(m, typesys.BorrowMode.PEEK),
        "PointerType": PointerType(pointee_type=m),
        "StructType": StructType(name="S", fields=(("f", m),)),
        "EnumType": EnumType(
            name="E", variants=(EnumVariantInfo(name="V", associated_types=(m,)),)),
        "FunctionType": FunctionType(
            param_types=(m,), ok_type=BuiltinType.I32, err_type=BuiltinType.I32),
        "GenericTypeRef": GenericTypeRef(base_name="Box", type_args=(m,)),
        "TypePack": TypePack(types=(m,)),
        "GenericStructType": GenericStructType(
            name="G", type_params=(TypeParameter("T"),), fields=(("f", m),)),
        "GenericEnumType": GenericEnumType(
            name="GE", type_params=(TypeParameter("T"),),
            variants=(EnumVariantInfo(name="V", associated_types=(m,)),)),
    }


def test_every_type_kind_is_walked_or_declared_terminal():
    # `UnknownType` is neither: it is a bare NAME that resolves through the tables, and
    # `test_an_unknown_name_resolves_through_the_tables` is its coverage.
    covered = set(_every_composite()) | set(TERMINAL_KINDS) | {"UnknownType"}
    missing = sorted((_type_union_members() | OFF_UNION_KINDS) - covered)
    assert not missing, (
        f"walk_named_types has no answer for: {missing}.\n"
        "A kind that is neither walked nor in TERMINAL_KINDS is skipped silently, so "
        "every predicate built on the walk inherits the hole."
    )


def test_a_composite_yields_what_it_holds():
    """The walk must actually arrive, not merely have an arm."""
    for kind, value in _every_composite().items():
        reached = list(walk_named_types(value))
        assert any(isinstance(t, ForeignPtrType) for t in reached), (
            f"walk_named_types({kind}) never reached the type it holds. "
            f"Reached: {[str(t) for t in reached]}"
        )


def test_a_terminal_yields_only_itself():
    for name in TERMINAL_KINDS:
        assert name in (_type_union_members() | OFF_UNION_KINDS), (
            f"TERMINAL_KINDS names '{name}', which is not a type kind. Dead entry or typo."
        )
    assert list(walk_named_types(BuiltinType.I32)) == [BuiltinType.I32]
    assert list(walk_named_types(TypeParameter("T"))) == [TypeParameter("T")]


def test_an_unknown_name_resolves_through_the_tables():
    """A bare name is the ONE kind that needs the tables to go any further."""
    hidden = StructType(name="Hidden", fields=(("f", _marker()),))
    reached = list(walk_named_types(UnknownType("Hidden"), {"Hidden": hidden}, {}))
    assert any(isinstance(t, ForeignPtrType) for t in reached), (
        "an UnknownType naming a table entry must resolve and be walked through"
    )
    # With no tables it is a leaf, not a crash: the caller may not have them.
    assert list(walk_named_types(UnknownType("Hidden"))) == [UnknownType("Hidden")]


def test_a_cycle_terminates():
    """A self-referential struct must not spin. Nominal identity makes this reachable."""
    node = StructType(name="Node", fields=(("next", UnknownType("Node")),))
    reached = list(walk_named_types(node, {"Node": node}, {}))
    assert reached, "a cyclic type yielded nothing"
    assert sum(1 for t in reached if isinstance(t, StructType)) == 1, (
        "a cyclic type was entered more than once"
    )
