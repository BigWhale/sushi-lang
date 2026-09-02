"""The (provenance x type class) grid, cell by cell."""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ownership import (
    ConsumingUse,
    Ownership,
    Provenance,
    TypeClass,
    classify,
    is_get_out_container,
    is_own_type,
    type_class_of,
)
from sushi_lang.semantics.typesys import (
    ArrayType,
    BorrowMode,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    EnumVariantInfo,
    FunctionType,
    GenericTypeRef,
    ReferenceType,
    StructType,
    UnknownType,
)

# The Drop set `type_class_of` reads (ruling R2a). This module's corpus
# declares no resource type; the resource half of the predicate is
# `tests/unit/test_cleanup_predicates_agree.py`.
NO_DROPS: frozenset = frozenset()


I32 = BuiltinType.I32
STR = BuiltinType.STRING


# --- The table ---------------------------------------------------------------------

# docs/design/ownership-conventions.md section 4.3. Written out longhand on purpose: a
# test that recomputed the table from the same dict it is checking would assert nothing.
EXPECTED = {
    (Provenance.OWNED,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.OWNED,    TypeClass.MOVE):  Ownership.MOVE,
    (Provenance.BORROWED, TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.BORROWED, TypeClass.MOVE):  Ownership.REJECT,
    (Provenance.FRESH,    TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.FRESH,    TypeClass.MOVE):  Ownership.ADOPT,
}


@pytest.mark.parametrize(("cell", "expected"), sorted(EXPECTED.items(), key=str))
def test_every_cell(cell, expected):
    provenance, type_class = cell
    assert classify(provenance, type_class) is expected


def test_the_grid_is_total():
    """No (provenance, type class) pair may be undefined."""
    for provenance in Provenance:
        for type_class in TypeClass:
            assert classify(provenance, type_class) in Ownership


def test_borrowed_move_is_the_rejected_cell():
    """The one cell every shipped bug in this family got wrong (#238, #250, #256, #277)."""
    rejected = [c for c, o in EXPECTED.items() if o is Ownership.REJECT]
    assert rejected == [(Provenance.BORROWED, TypeClass.MOVE)]


def test_the_compiler_inserts_no_deep_copy_at_a_read():
    """A read through a live owner is a BORROW, and consuming one is rejected (#242)."""
    assert classify(Provenance.BORROWED, TypeClass.MOVE) is Ownership.REJECT
    assert not hasattr(Ownership, "COPY"), (
        "Ownership.COPY is back. Phase 9 deleted it so that the compiler inserts no deep "
        "copy of its own anywhere; `.clone()` is the only one in a Sushi program."
    )
    assert [p.name for p in Provenance] == ["OWNED", "BORROWED", "FRESH"]
    assert [c.name for c in TypeClass] == ["PLAIN", "MOVE"]


def test_fresh_never_copies():
    """Nothing owns a fresh value, so copying one orphans the original -- a leak."""
    for type_class in TypeClass:
        assert classify(Provenance.FRESH, type_class) is Ownership.ADOPT


# --- Type classification -----------------------------------------------------------

def _struct(name, *fields):
    return StructType(name=name, fields=tuple(fields))


def test_plain_types():
    for ty in (I32, BuiltinType.BOOL, BuiltinType.F64,
               _struct("P", ("x", I32), ("y", I32)),
               ArrayType(base_type=I32, size=3)):
        assert type_class_of(ty, NO_DROPS) is TypeClass.PLAIN


def test_string_and_string_composites_move():
    """The Phase 9 flip, stated at the classification layer."""
    for ty in (STR,
               _struct("N", ("s", STR)),
               _struct("M", ("i", I32), ("s", STR)),
               ArrayType(base_type=STR, size=2),
               DynamicArrayType(base_type=STR)):
        assert type_class_of(ty, NO_DROPS) is TypeClass.MOVE, ty


def test_move_types():
    owning_struct = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    for ty in (DynamicArrayType(base_type=I32),
               GenericTypeRef(base_name="List", type_args=[I32]),
               GenericTypeRef(base_name="Own", type_args=[I32]),
               owning_struct,
               # A fixed array inherits move-ness from its elements.
               ArrayType(base_type=owning_struct, size=2)):
        assert type_class_of(ty, NO_DROPS) is TypeClass.MOVE, ty


def test_a_type_that_owns_two_kinds_of_heap_still_moves():
    """A struct holding BOTH a string and a dynamic array MOVES."""
    both = _struct("B", ("s", STR), ("items", DynamicArrayType(base_type=I32)))
    assert type_class_of(both, NO_DROPS) is TypeClass.MOVE


def test_enum_payload_is_walked():
    owning = EnumType(name="E", variants=(
        EnumVariantInfo(name="Has", associated_types=(DynamicArrayType(base_type=I32),)),
        EnumVariantInfo(name="No", associated_types=()),
    ))
    stringy = EnumType(name="F", variants=(
        EnumVariantInfo(name="Msg", associated_types=(STR,)),
    ))
    assert type_class_of(owning, NO_DROPS) is TypeClass.MOVE
    assert type_class_of(stringy, NO_DROPS) is TypeClass.MOVE   # was COPY before Phase 9


def test_unresolved_named_type_is_resolved_before_classifying():
    """An UnknownType answers PLAIN unless resolved -- which would alias an owning value."""
    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    named = UnknownType(name="W")
    table = {"W": owning}

    def resolve(ty):
        return table.get(getattr(ty, "name", None))

    assert type_class_of(named, NO_DROPS) is TypeClass.PLAIN          # no resolver: the hazard
    assert type_class_of(named, NO_DROPS, resolve) is TypeClass.MOVE


def test_a_resolver_that_misses_leaves_the_type_alone():
    """A miss must not blank the type -- `None` reads as PLAIN, which aliases an owner."""
    def resolve(_ty):
        return None

    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    assert type_class_of(owning, NO_DROPS, resolve) is TypeClass.MOVE
    assert type_class_of(UnknownType(name="W"), NO_DROPS, resolve) is TypeClass.PLAIN


def test_reference_classifies_as_its_referent():
    """A reference carries the type class of the value it names, in both modes."""
    owning = DynamicArrayType(base_type=I32)
    for mode in (BorrowMode.PEEK, BorrowMode.POKE):
        assert type_class_of(ReferenceType(referenced_type=owning,
                                           mutability=mode), NO_DROPS) is TypeClass.MOVE
    for mode in (BorrowMode.PEEK, BorrowMode.POKE):
        assert type_class_of(ReferenceType(referenced_type=I32,
                                           mutability=mode), NO_DROPS) is TypeClass.PLAIN


def test_reference_to_string_moves():
    """`string` owns heap at the type level, so a borrow of one classifies MOVE."""
    assert type_class_of(ReferenceType(referenced_type=STR,
                                       mutability=BorrowMode.PEEK), NO_DROPS) is TypeClass.MOVE


def test_reference_referent_is_resolved_through_the_reference():
    """The resolver must reach the referent, not stop at the reference wrapper."""
    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))

    def resolve(ty):
        return owning if getattr(ty, "name", None) == "W" else ty

    ref = ReferenceType(referenced_type=UnknownType(name="W"), mutability=BorrowMode.POKE)
    assert type_class_of(ref, NO_DROPS, resolve) is TypeClass.MOVE


def test_consuming_a_reference_is_rejected():
    """The cell that follows: a borrow of an owning value cannot be given away."""
    owning = ReferenceType(referenced_type=DynamicArrayType(base_type=I32),
                           mutability=BorrowMode.POKE)
    assert classify(Provenance.BORROWED, type_class_of(owning, NO_DROPS)) is Ownership.REJECT


def test_consuming_a_reference_to_a_plain_value_adopts():
    """(BORROWED, PLAIN) stays ADOPT: copying a value that owns no heap transfers nothing."""
    plain = ReferenceType(referenced_type=I32, mutability=BorrowMode.PEEK)
    assert classify(Provenance.BORROWED, type_class_of(plain, NO_DROPS)) is Ownership.ADOPT


def test_none_is_plain():
    """A binding with no recorded type must not be guessed at as owning."""
    assert type_class_of(None, NO_DROPS) is TypeClass.PLAIN


def test_every_function_value_moves_whatever_its_captures_say():
    """Capture metadata may be absent, so it must not decide ownership."""
    declared = FunctionType(param_types=(I32,), ok_type=I32, err_type=UnknownType(name="StdError"))
    capturing = FunctionType(param_types=(I32,), ok_type=I32,
                             err_type=UnknownType(name="StdError"),
                             captures=(("k", I32),))
    assert type_class_of(declared, NO_DROPS) is TypeClass.MOVE
    assert type_class_of(capturing, NO_DROPS) is TypeClass.MOVE


def test_recursive_type_terminates():
    """A self-referential struct must not send the string walk into infinite recursion."""
    node = StructType(name="Node", fields=())
    node.__dict__["fields"] = (("next", GenericTypeRef(base_name="Own", type_args=[node])),
                               ("label", STR))
    assert type_class_of(node, NO_DROPS) is TypeClass.MOVE


# --- Get-out detection ---------------------------------------------------------------

def test_is_own_type():
    """`Own@(T)` alone, by name. One arm of `is_get_out_container` since #242."""
    assert is_own_type(GenericTypeRef(base_name="Own", type_args=[I32]))
    assert is_own_type(StructType(name="Own<i32>", fields=()))
    assert not is_own_type(GenericTypeRef(base_name="List", type_args=[I32]))
    assert not is_own_type(StructType(name="Owner", fields=()))  # prefix, not the type
    assert not is_own_type(None)


def test_is_get_out_container():
    """Every container keeps the element its `.get()` hands back, so every one is a view."""
    for ty in (GenericTypeRef(base_name="Own", type_args=[I32]),
               GenericTypeRef(base_name="List", type_args=[I32]),
               GenericTypeRef(base_name="HashMap", type_args=[STR, I32]),
               StructType(name="Own<i32>", fields=()),
               StructType(name="List<i32>", fields=()),
               StructType(name="HashMap<string, i32>", fields=()),
               DynamicArrayType(base_type=I32),
               ArrayType(base_type=I32, size=3)):
        assert is_get_out_container(ty), ty

    for ty in (None, I32, STR,
               _struct("Holder", ("data", DynamicArrayType(base_type=I32))),
               StructType(name="Owner", fields=())):     # prefix, not the type
        assert not is_get_out_container(ty), ty

    # A borrow of a container is still a container.
    assert is_get_out_container(
        ReferenceType(referenced_type=GenericTypeRef(base_name="List", type_args=[I32]),
                      mutability=BorrowMode.PEEK))


def test_consuming_use_set_is_the_documented_fifteen():
    """The closed set. A sixteenth position must be declared here before it can exist."""
    assert {u.name for u in ConsumingUse} == {
        "CALL_ARG", "LET", "REBIND", "FIELD_ASSIGN", "STRUCT_FIELD", "ENUM_PAYLOAD",
        "ARRAY_ELEMENT", "ELEMENT_ASSIGN", "CONTAINER_INSERT", "RETURN", "CAPTURE",
        "OWN_ALLOC", "MATCH_SCRUTINEE", "RECEIVER", "TRY",
    }
