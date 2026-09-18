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


# --- inline_only: the walk the finite-types pass reads (#679) ----------------------


def test_every_kind_is_inline_or_an_indirection():
    """`inline_only` must have an answer for every kind, the way the walk itself does."""
    from sushi_lang.semantics.type_walk import INLINE_KINDS

    known = _type_union_members() | OFF_UNION_KINDS
    strays = sorted(INLINE_KINDS - known)
    assert not strays, f"INLINE_KINDS names something that is not a type kind: {strays}"


def test_inline_only_enters_what_a_value_stores_by_value():
    """A fixed array and a declaration hold their contents inline."""
    from sushi_lang.semantics.type_walk import INLINE_KINDS

    for kind, value in _every_composite().items():
        if kind not in INLINE_KINDS:
            continue
        reached = list(walk_named_types(value, inline_only=True))
        assert any(isinstance(t, ForeignPtrType) for t in reached), (
            f"inline_only skipped what {kind} stores by value"
        )


def test_inline_only_stops_at_an_indirection():
    """A heap buffer, a pointer and a fat pointer are not size; the walk stops there."""
    from sushi_lang.semantics.type_walk import INLINE_KINDS

    for kind, value in _every_composite().items():
        if kind in INLINE_KINDS:
            continue
        reached = list(walk_named_types(value, inline_only=True))
        assert reached == [value], (
            f"inline_only entered {kind}, which holds what it names behind an "
            f"indirection. Reached: {[str(t) for t in reached]}"
        )


def test_the_finite_types_pass_reads_the_shared_walk():
    """Its exclusion is a parameter on the one walk, not a private reimplementation."""
    import inspect

    from sushi_lang.semantics.passes import finite_types

    source = inspect.getsource(finite_types)
    assert "walk_named_types" in source, (
        "passes/finite_types.py recurses over a type by hand again"
    )
    assert "inline_only=True" in source, (
        "passes/finite_types.py must say which walk it wants, on the shared walk"
    )


def test_a_dynamic_array_of_a_struct_is_not_inline_containment():
    """The rule the finite-types pass depends on: `Node[]` does not contain `Node`."""
    node = StructType(name="Node", fields=())
    inline = list(walk_named_types(ArrayType(base_type=node, size=2), inline_only=True,
                                   through_declarations=False))
    assert node in inline, "a FIXED array stores its elements inline"

    indirect = list(walk_named_types(DynamicArrayType(base_type=node), inline_only=True,
                                     through_declarations=False))
    assert node not in indirect, "a dynamic array owns a heap buffer, which is an indirection"


# --- the resolver rebuilds the same shape the walk reads (#716) --------------------

# Each kind that HOLDS one type, with the way to build one and the way to read what it
# holds back. `resolve_type_recursively` had no arm for any of the three, so a name
# nested in one came back unresolved while the same name in an array came back as the
# table entry -- two spellings of one interned name (docs/design/type-identity.md).
HOLDS_ONE_TYPE = {
    "ReferenceType": (
        lambda held: ReferenceType(held, typesys.BorrowMode.PEEK),
        lambda ty: ty.referenced_type,
    ),
    "PointerType": (
        lambda held: PointerType(pointee_type=held),
        lambda ty: ty.pointee_type,
    ),
    "IteratorType": (
        lambda held: IteratorType(element_type=held),
        lambda ty: ty.element_type,
    ),
}


def _node_tables() -> tuple[StructType, dict, dict]:
    node = StructType(name="Node", fields=())
    return node, {"Node": node}, {}


def test_the_resolver_resolves_a_name_every_kind_holds():
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    node, structs, enums = _node_tables()
    unresolved = []
    for kind, (build, held_of) in HOLDS_ONE_TYPE.items():
        resolved = resolve_type_recursively(build(UnknownType("Node")), structs, enums)
        if held_of(resolved) is not node:
            unresolved.append(f"{kind} holds {held_of(resolved)!r}")
    assert not unresolved, (
        "the resolver left a written name unresolved: " + "; ".join(unresolved) + ".\n"
        "The same name in an array resolves to the table entry, so one type has two "
        "spellings, which is what the intern seam exists to prevent."
    )


def test_the_resolver_keeps_what_a_reference_declares():
    """`dataclasses.replace`, so the borrow mode rides along (#368 is the same rule)."""
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    node, structs, enums = _node_tables()
    for mode in (typesys.BorrowMode.PEEK, typesys.BorrowMode.POKE):
        resolved = resolve_type_recursively(
            ReferenceType(UnknownType("Node"), mode), structs, enums)
        assert resolved.mutability is mode, (
            f"a rebuilt reference lost its {mode} marker"
        )


def test_the_resolver_rebuilds_nothing_that_is_already_resolved():
    """The resolve pass runs again at every late intern; an unchanged type stays itself."""
    from sushi_lang.semantics.type_resolution import resolve_type_recursively

    node, structs, enums = _node_tables()
    for kind, (build, _held_of) in HOLDS_ONE_TYPE.items():
        already = build(node)
        assert resolve_type_recursively(already, structs, enums) is already, (
            f"a {kind} that holds the table entry was rebuilt anyway"
        )
