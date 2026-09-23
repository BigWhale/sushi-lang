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


# --- the map: the same arm table, the other direction (#718) -----------------------


def _swap_the_marker(ty):
    """A resolve function with a visible answer: the marker becomes a builtin."""
    return BuiltinType.I64 if isinstance(ty, ForeignPtrType) else ty


def test_the_arm_table_has_an_answer_for_every_kind():
    """One table, both directions. A kind it does not name is walked by neither."""
    from sushi_lang.semantics.type_walk import COMPOSITE_KINDS, DECLARATION_KINDS

    known = _type_union_members() | OFF_UNION_KINDS
    covered = set(COMPOSITE_KINDS) | set(DECLARATION_KINDS) | set(TERMINAL_KINDS)
    missing = sorted(known - covered - {"UnknownType"})
    assert not missing, (
        f"the arm table has no answer for: {missing}. The walk and the map both read it, "
        "so a kind it misses is skipped in both directions."
    )
    strays = sorted(covered - known)
    assert not strays, f"the arm table names something that is not a type kind: {strays}"


def test_the_map_rebuilds_what_a_composite_holds():
    """The map must arrive everywhere the walk does."""
    from sushi_lang.semantics.type_walk import COMPOSITE_KINDS, map_named_types

    for kind, value in _every_composite().items():
        if kind not in COMPOSITE_KINDS:
            continue
        mapped = map_named_types(value, _swap_the_marker)
        reached = list(walk_named_types(mapped))
        assert BuiltinType.I64 in reached, (
            f"map_named_types({kind}) never reached the type it holds. "
            f"Reached: {[str(t) for t in reached]}"
        )
        assert not any(isinstance(t, ForeignPtrType) for t in reached), (
            f"map_named_types({kind}) rebuilt around what it holds and kept the old type"
        )


def test_the_map_stops_at_a_declaration():
    """Type identity is NOMINAL: a named type is looked up, never rebuilt."""
    from sushi_lang.semantics.type_walk import DECLARATION_KINDS, map_named_types

    for kind, value in _every_composite().items():
        if kind not in DECLARATION_KINDS:
            continue
        assert map_named_types(value, _swap_the_marker) is value, (
            f"map_named_types rebuilt a {kind}. The table is the sole authority for what "
            "a named type holds, and a second instance of one name is the #240 hazard."
        )


def test_the_map_answers_the_same_object_when_nothing_moves():
    """The resolve pass runs again at every late intern; an unchanged type stays itself."""
    from sushi_lang.semantics.type_walk import map_named_types

    for kind, value in _every_composite().items():
        assert map_named_types(value, lambda ty: ty) is value, (
            f"map_named_types rebuilt an unchanged {kind}"
        )


def test_the_map_carries_what_a_kind_declares_beside_its_types():
    """`dataclasses.replace`, so a borrow mode and a fn type's metadata ride along (#368)."""
    from sushi_lang.semantics.param_modes import ParamMode
    from sushi_lang.semantics.type_walk import map_named_types

    reference = ReferenceType(_marker(), typesys.BorrowMode.PEEK)
    assert map_named_types(reference, _swap_the_marker).mutability is (
        typesys.BorrowMode.PEEK)

    fn = FunctionType(param_types=(_marker(),), ok_type=BuiltinType.I32,
                      err_type=BuiltinType.I32, captures=("c",),
                      param_modes=(ParamMode.NOM,))
    mapped = map_named_types(fn, _swap_the_marker)
    assert mapped.captures == ("c",)
    assert mapped.modes == (ParamMode.NOM,)


def test_the_resolver_is_one_call_into_the_map():
    """One walk, two directions. Two arm sets is how the resolver grew its holes."""
    import inspect

    from sushi_lang.semantics import type_resolution

    source = inspect.getsource(type_resolution.resolve_type_recursively)
    assert "map_named_types" in source, (
        "type_resolution.resolve_type_recursively recurses over a type by hand again"
    )
    for built in ("ArrayType(", "ReferenceType(", "IteratorType(", "GenericTypeRef(",
                  "replace("):
        assert built not in source, (
            f"the resolver builds a {built[:-1]} itself; the shared map owns that arm"
        )


# --- the ownership hooks: stop, resolve, a struct's type arguments (#781) ----------


def test_stop_yields_a_type_and_enters_nothing_it_holds():
    """The prune hook, for every composite and every declaration alike."""
    for kind, value in _every_composite().items():
        reached = list(walk_named_types(value, stop=lambda ty, v=value: ty is v))
        assert reached == [value], (
            f"stop did not prune {kind}. Reached: {[str(t) for t in reached]}"
        )


def test_resolve_enters_the_declaration_a_spelling_names():
    """A bare name and a generic reference both reach what they name."""
    hidden = StructType(name="Hidden", fields=(("f", _marker()),))
    spellings = {
        "UnknownType": UnknownType("Hidden"),
        "GenericTypeRef": GenericTypeRef(base_name="Hidden", type_args=(BuiltinType.I32,)),
    }
    for kind, spelling in spellings.items():
        reached = list(walk_named_types(spelling, resolve=lambda ty: hidden))
        assert hidden in reached and any(isinstance(t, ForeignPtrType) for t in reached), (
            f"resolve did not enter the declaration a {kind} names"
        )
        if kind == "GenericTypeRef":
            assert BuiltinType.I32 not in reached, (
                "a resolved generic reference was entered through its arguments as well"
            )


def test_an_unresolved_generic_reference_is_entered_through_its_arguments():
    ref = GenericTypeRef(base_name="Nope", type_args=(_marker(),))
    for resolve in (lambda ty: ty, lambda ty: None):
        reached = list(walk_named_types(ref, resolve=resolve))
        assert any(isinstance(t, ForeignPtrType) for t in reached), (
            "a generic reference that does not resolve must still reach its arguments"
        )


def test_a_structs_type_arguments_are_entered_only_on_request():
    """A container keeps its element type there; a size rule must not see it (#679)."""
    box = StructType(name="List<X>", fields=(("data", BuiltinType.I32),),
                     generic_args=(_marker(),))
    plain = list(walk_named_types(box))
    assert not any(isinstance(t, ForeignPtrType) for t in plain)
    asked = list(walk_named_types(box, struct_type_args=True))
    assert any(isinstance(t, ForeignPtrType) for t in asked)


def test_the_ownership_predicates_read_the_shared_walk():
    """Two hand-rolled recursions disagreed on a generic reference (#781)."""
    import inspect

    for predicate in (typesys.owns_resource, typesys.holds_declared_resource):
        source = inspect.getsource(predicate)
        body = source.split('"""', 2)[-1]
        assert "walk_named_types" in body, (
            f"{predicate.__name__} recurses over a type by hand again"
        )
        assert f"{predicate.__name__}(" not in body, (
            f"{predicate.__name__} calls itself; the shared walk owns the recursion"
        )


def test_the_ownership_predicates_agree_on_an_unresolved_generic_reference():
    handle = StructType(name="Handle", fields=(("fd", BuiltinType.I32),))
    ref = GenericTypeRef(base_name="Result", type_args=(handle, BuiltinType.I32))
    drops = frozenset({"Handle"})
    assert typesys.holds_declared_resource(ref, drops)
    assert typesys.owns_resource(ref, drops), (
        "owns_resource must see the arguments of a reference it cannot resolve, the way "
        "holds_declared_resource does"
    )
