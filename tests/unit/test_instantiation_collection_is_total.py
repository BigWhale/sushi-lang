"""Every generic instantiation a TYPE names is collected, over the ONE type walk.

Two collectors read types for instantiations: the instantiate pass reads WRITTEN types
(a `GenericTypeRef`), and the monomorphizer reads SUBSTITUTED ones (a `StructType` that
carries its generic base). Both used to recurse by hand, and both had holes: a
`peek Box@(string)` parameter, a `fn(i32) -> Box@(string)` parameter and a struct field
of a function type each named a type the compiler then reported as unknown, CE2001 for a
type the program declares (#603). Both ride `walk_named_types` now, so a kind added to
the seam is covered here by construction.
"""
from __future__ import annotations

import typing

from sushi_lang.semantics import typesys
from sushi_lang.semantics.type_walk import TERMINAL_KINDS
from sushi_lang.semantics.typesys import (
    ArrayType, BorrowMode, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    FunctionType, IteratorType, PointerType, ReferenceType, StructType, UnknownType,
)
from sushi_lang.semantics.generics.types import (
    GenericEnumType, GenericStructType, GenericTypeRef, TypeParameter, TypePack,
)
from sushi_lang.semantics.generics.instantiate.type_collection import (
    collect_type_instantiations,
)
from sushi_lang.semantics.generics.monomorphize.functions import (
    extract_type_instantiations,
)
from sushi_lang.semantics.type_resolution import TypeResolver


STR = BuiltinType.STRING
EXPECTED = ("Box", (STR,))

# The two spellings of one instantiation. A written type spells it; a monomorphized type
# IS it and carries the base name it came from.
WRITTEN = GenericTypeRef(base_name="Box", type_args=(STR,))
SUBSTITUTED = StructType(
    name="Box<string>", fields=(("item", STR),), generic_base="Box", generic_args=(STR,))

# The kinds that hold types but are not in the `Type` union -- see
# `tests/unit/test_type_walk_is_total.py`, which owns that distinction.
OFF_UNION_KINDS = {"GenericStructType", "GenericEnumType", "TypePack"}


def _wrappers(inner) -> dict[str, object]:
    """One value per composite kind, each hiding `inner` exactly one level down."""
    return {
        "ArrayType": ArrayType(base_type=inner, size=3),
        "DynamicArrayType": DynamicArrayType(base_type=inner),
        "IteratorType": IteratorType(element_type=inner),
        "ReferenceType": ReferenceType(inner, BorrowMode.PEEK),
        "PointerType": PointerType(pointee_type=inner),
        "StructType": StructType(name="Holder", fields=(("f", inner),)),
        "EnumType": EnumType(
            name="Wrap", variants=(EnumVariantInfo(name="V", associated_types=(inner,)),)),
        "FunctionType": FunctionType(
            param_types=(inner,), ok_type=BuiltinType.I32, err_type=BuiltinType.I32),
        "GenericTypeRef": GenericTypeRef(base_name="Cell", type_args=(inner,)),
        "TypePack": TypePack(types=(inner,)),
        "GenericStructType": GenericStructType(
            name="G", type_params=(TypeParameter("T"),), fields=(("f", inner),)),
        "GenericEnumType": GenericEnumType(
            name="GE", type_params=(TypeParameter("T"),),
            variants=(EnumVariantInfo(name="V", associated_types=(inner,)),)),
    }


def _collect_written(ty) -> set:
    """What the instantiate pass's collector finds in a written type."""
    found: set = set()
    collect_type_instantiations(
        ty, TypeResolver({}, {}), found, structs={}, enums={}, visited=set())
    return found


def _collect_substituted(ty) -> set:
    """What the monomorphizer finds in a substituted signature type."""
    found: set = set()
    extract_type_instantiations(ty, found)
    return found


def test_the_wrapper_set_covers_every_kind():
    union = {t.__name__ for t in typing.get_args(typesys.Type)} | OFF_UNION_KINDS
    # `UnknownType` is a bare NAME, covered by its own test below.
    covered = set(_wrappers(STR)) | set(TERMINAL_KINDS) | {"UnknownType"}
    missing = sorted(union - covered)
    assert not missing, (
        f"no instantiation-collection coverage for: {missing}.\n"
        "A kind a collector cannot see is a false CE2001 for a declared type."
    )


def test_a_written_instance_is_found_under_every_kind():
    for kind, wrapper in _wrappers(WRITTEN).items():
        assert EXPECTED in _collect_written(wrapper), (
            f"a written Box@(string) inside a {kind} was not collected"
        )


def test_a_substituted_instance_is_found_under_every_kind():
    for kind, wrapper in _wrappers(SUBSTITUTED).items():
        assert EXPECTED in _collect_substituted(wrapper), (
            f"a monomorphized Box<string> inside a {kind} was not collected"
        )


def test_a_bare_name_resolves_through_the_tables():
    """The one kind that needs the tables: a name that stands for a declaration."""
    holder = StructType(name="Holder", fields=(("f", WRITTEN),))
    found: set = set()
    collect_type_instantiations(
        UnknownType("Holder"), TypeResolver({"Holder": holder}, {}), found,
        structs={"Holder": holder}, enums={}, visited=set())
    assert EXPECTED in found


def test_a_cycle_terminates():
    """A struct that holds an instance of itself must not spin either collector."""
    node = StructType(name="Node", fields=(("child", GenericTypeRef("Box", (UnknownType("Node"),))),))
    found: set = set()
    collect_type_instantiations(
        node, TypeResolver({"Node": node}, {}), found,
        structs={"Node": node}, enums={}, visited=set())
    assert ("Box", (node,)) in found

    boxed = StructType(
        name="Box<Node>", fields=(("item", node),), generic_base="Box", generic_args=(node,))
    substituted_node = StructType(name="Node", fields=(("child", boxed),))
    found = set()
    extract_type_instantiations(substituted_node, found)
    assert ("Box", (node,)) in found
