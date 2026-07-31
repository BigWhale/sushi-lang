"""The (provenance x type class) grid, cell by cell.

Every shipped bug in the move/clone family lives in this grid, and until
`semantics/ownership.py` existed none of them could be unit-tested: each of the eleven
consuming uses fused deciding with emitting, so the decision was never a value. This
file is the reason the decision is now a value.

The grid is 4 x 3 = 12 cells and every one is asserted below. A cell asserted here and
implemented differently at a position is a bug at that position, not a disagreement --
which is the whole point of having one table.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ownership import (
    ConsumingUse,
    Ownership,
    Provenance,
    TypeClass,
    classify,
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


I32 = BuiltinType.I32
STR = BuiltinType.STRING


# --- The table ---------------------------------------------------------------------

# docs/design/ownership-conventions.md section 4.3. Written out longhand on purpose: a
# test that recomputed the table from the same dict it is checking would assert nothing.
EXPECTED = {
    (Provenance.OWNED,         TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.OWNED,         TypeClass.COPY):  Ownership.COPY,
    (Provenance.OWNED,         TypeClass.MOVE):  Ownership.MOVE,
    (Provenance.BORROWED,      TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.BORROWED,      TypeClass.COPY):  Ownership.COPY,
    (Provenance.BORROWED,      TypeClass.MOVE):  Ownership.REJECT,
    (Provenance.THROUGH_OWNER, TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.THROUGH_OWNER, TypeClass.COPY):  Ownership.COPY,
    (Provenance.THROUGH_OWNER, TypeClass.MOVE):  Ownership.COPY,
    (Provenance.FRESH,         TypeClass.PLAIN): Ownership.ADOPT,
    (Provenance.FRESH,         TypeClass.COPY):  Ownership.ADOPT,
    (Provenance.FRESH,         TypeClass.MOVE):  Ownership.ADOPT,
}


@pytest.mark.parametrize(("cell", "expected"), sorted(EXPECTED.items(), key=str))
def test_every_cell(cell, expected):
    provenance, type_class = cell
    assert classify(provenance, type_class) is expected


def test_the_grid_is_total():
    """No (provenance, type class) pair may be undefined.

    A missing cell is a KeyError at compile time for some user program, and the shape of
    the bug this whole module exists to prevent is a position that silently did nothing.
    """
    for provenance in Provenance:
        for type_class in TypeClass:
            assert classify(provenance, type_class) in Ownership


def test_borrowed_move_is_the_rejected_cell():
    """The one cell every shipped bug in this family got wrong (#238, #250, #256, #277).

    Consuming a borrowed binding whose type owns heap is CE2411; `.clone()` is the escape.
    """
    rejected = [c for c, o in EXPECTED.items() if o is Ownership.REJECT]
    assert rejected == [(Provenance.BORROWED, TypeClass.MOVE)]


def test_owning_rows_are_deliberately_asymmetric():
    """BORROWED is rejected; THROUGH_OWNER copies. Not an oversight -- section 4.3.

    A borrowed binding has a shorter lifetime than its owner and a visible alternative at
    the use site. Making every owning field read an error would force `.clone()` on every
    `s.field` with no escape until let-borrow bindings exist -- that is #242, deferred.
    """
    assert classify(Provenance.BORROWED, TypeClass.MOVE) is Ownership.REJECT
    assert classify(Provenance.THROUGH_OWNER, TypeClass.MOVE) is Ownership.COPY


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
        assert type_class_of(ty) is TypeClass.PLAIN


def test_copy_types():
    """`string` owns heap but is a copy type (docs/design/string-representation.md)."""
    for ty in (STR,
               _struct("N", ("s", STR)),
               _struct("M", ("i", I32), ("s", STR)),
               ArrayType(base_type=STR, size=2),
               DynamicArrayType(base_type=STR)):
        # A dynamic array is itself an owning resource, so it MOVES regardless of element.
        expected = TypeClass.MOVE if isinstance(ty, DynamicArrayType) else TypeClass.COPY
        assert type_class_of(ty) is expected, ty


def test_move_types():
    owning_struct = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    for ty in (DynamicArrayType(base_type=I32),
               GenericTypeRef(base_name="List", type_args=[I32]),
               GenericTypeRef(base_name="Own", type_args=[I32]),
               owning_struct,
               # A fixed array inherits move-ness from its elements.
               ArrayType(base_type=owning_struct, size=2)):
        assert type_class_of(ty) is TypeClass.MOVE, ty


def test_move_beats_copy_when_a_type_is_both():
    """A struct holding BOTH a string and a dynamic array MOVES; it does not copy.

    Order matters in `type_class_of`: asking "contains a string?" first would classify
    this as COPY and alias the array buffer.
    """
    both = _struct("B", ("s", STR), ("items", DynamicArrayType(base_type=I32)))
    assert type_class_of(both) is TypeClass.MOVE


def test_enum_payload_is_walked():
    owning = EnumType(name="E", variants=(
        EnumVariantInfo(name="Has", associated_types=(DynamicArrayType(base_type=I32),)),
        EnumVariantInfo(name="No", associated_types=()),
    ))
    stringy = EnumType(name="F", variants=(
        EnumVariantInfo(name="Msg", associated_types=(STR,)),
    ))
    assert type_class_of(owning) is TypeClass.MOVE
    assert type_class_of(stringy) is TypeClass.COPY


def test_unresolved_named_type_is_resolved_before_classifying():
    """An UnknownType answers PLAIN unless resolved -- which would alias an owning value.

    `type_moves_by_value` returns False for an UnknownType by design ("Pass 2 rejects
    unresolved types"), so skipping the resolver here is exactly how an owning struct
    gets classified as owning nothing.
    """
    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    named = UnknownType(name="W")
    table = {"W": owning}

    def resolve(ty):
        return table.get(getattr(ty, "name", None))

    assert type_class_of(named) is TypeClass.PLAIN          # no resolver: the hazard
    assert type_class_of(named, resolve) is TypeClass.MOVE


def test_a_resolver_that_misses_leaves_the_type_alone():
    """A miss must not blank the type -- `None` reads as PLAIN, which aliases an owner."""
    def resolve(_ty):
        return None

    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    assert type_class_of(owning, resolve) is TypeClass.MOVE
    assert type_class_of(UnknownType(name="W"), resolve) is TypeClass.PLAIN


def test_reference_is_never_an_owner():
    """A `&peek`/`&poke` parameter is a borrow; the value it names is owned elsewhere."""
    owning = DynamicArrayType(base_type=I32)
    assert type_class_of(ReferenceType(referenced_type=owning,
                                       mutability=BorrowMode.PEEK)) is TypeClass.PLAIN


def test_none_is_plain():
    """A binding with no recorded type must not be guessed at as owning."""
    assert type_class_of(None) is TypeClass.PLAIN


def test_every_function_value_moves_whatever_its_captures_say():
    """Capture metadata may be absent, so it must not decide ownership.

    `FunctionType.__eq__` excludes `captures` from type identity. So a closure that
    reaches a position through a DECLARED type -- a `List@(fn(i32) -> i32)` element, a
    struct field, a parameter -- arrives with `captures=None` even though it does own a
    heap environment. Classifying on `captures` answered PLAIN there, the position
    adopted the value without marking the source moved, and the container and the local
    then freed the same environment (`tests/memory/test_closure_into_list.sushi`).

    Answering MOVE always is safe because the fat value resolves ownership at runtime:
    the destructor frees through `drop_ptr` and the deep copy duplicates through
    `clone_ptr`, and a non-capturing value carries null in both.
    """
    declared = FunctionType(param_types=(I32,), ok_type=I32, err_type=UnknownType(name="StdError"))
    capturing = FunctionType(param_types=(I32,), ok_type=I32,
                             err_type=UnknownType(name="StdError"),
                             captures=(("k", I32),))
    assert type_class_of(declared) is TypeClass.MOVE
    assert type_class_of(capturing) is TypeClass.MOVE


def test_recursive_type_terminates():
    """A self-referential struct must not send the string walk into infinite recursion."""
    node = StructType(name="Node", fields=())
    node.__dict__["fields"] = (("next", GenericTypeRef(base_name="Own", type_args=[node])),
                               ("label", STR))
    assert type_class_of(node) is TypeClass.MOVE


# --- Own detection -----------------------------------------------------------------

def test_is_own_type():
    """`Own@(T).get()` is a deref through a live owner; every other `.get()` is not.

    `Own` hands back the pointee uncopied, so its result is THROUGH_OWNER. Array / List /
    HashMap `.get()` deep-copy at the access site, so theirs is FRESH. Confusing the two
    was #256.
    """
    assert is_own_type(GenericTypeRef(base_name="Own", type_args=[I32]))
    assert is_own_type(StructType(name="Own<i32>", fields=()))
    assert not is_own_type(GenericTypeRef(base_name="List", type_args=[I32]))
    assert not is_own_type(StructType(name="Owner", fields=()))  # prefix, not the type
    assert not is_own_type(None)


def test_consuming_use_set_is_the_documented_eleven():
    """The closed set. A twelfth position must be declared here before it can exist."""
    assert {u.name for u in ConsumingUse} == {
        "CALL_ARG", "LET", "REBIND", "FIELD_ASSIGN", "STRUCT_FIELD", "ENUM_PAYLOAD",
        "ARRAY_ELEMENT", "CONTAINER_INSERT", "RETURN", "CAPTURE", "OWN_ALLOC",
    }
