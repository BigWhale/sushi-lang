"""The (provenance x type class) grid, cell by cell.

Every shipped bug in the move/clone family lives in this grid, and until
`semantics/ownership.py` existed none of them could be unit-tested: each of the eleven
consuming uses fused deciding with emitting, so the decision was never a value. This
file is the reason the decision is now a value.

The grid is 3 x 2 = 6 cells and every one is asserted below. A cell asserted here and
implemented differently at a position is a bug at that position, not a disagreement --
which is the whole point of having one table.

It has shrunk twice, and both times because an answer turned out to be unnecessary rather
than because it was wrong:

- **4 x 3 -> 3 x 3 (#242).** `Provenance.THROUGH_OWNER` copied where BORROWED rejects, and
  the only reason for the asymmetry was that a user could not bind a borrow and so had no
  escape from the rejection. Let-borrow bindings supply the escape, so the two rows became
  one row.
- **3 x 3 -> 3 x 2 (Phase 9).** `TypeClass.COPY` held the types that own heap but were
  duplicated rather than transferred -- `string` and string-only composites. Making a string
  MOVE removed the column, and `Ownership.COPY` went with it, because no cell needed it any
  more. A literal-bound string owns nothing at all and classifies PLAIN, which is a fact
  about the binding rather than the type (option B).

Between them those two deletions mean **the compiler inserts no deep copy anywhere**. Every
deep copy in a Sushi program is a `.clone()` the user wrote.
"""
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


def test_the_compiler_inserts_no_deep_copy_at_a_read():
    """A read through a live owner is a BORROW, and consuming one is rejected (#242).

    There is no cell that copies an owning value, so every deep copy of one in a Sushi
    program is a `.clone()` the user wrote. `Provenance.THROUGH_OWNER` used to hold the
    other answer; it no longer exists, and this asserts nothing brings it back under a
    different name.
    """
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
        assert type_class_of(ty) is TypeClass.PLAIN


def test_string_and_string_composites_move():
    """The Phase 9 flip, stated at the classification layer.

    Every one of these was `TypeClass.COPY` before: the compiler duplicated it at a sink and
    the source stayed usable. They MOVE now, and `.clone()` is the escape.

    The option-B exception -- a string bound straight from a literal owns nothing -- is NOT
    visible here and cannot be: it is a fact about a BINDING, not about a type, and lives on
    `BorrowState.owns_no_heap`.
    """
    for ty in (STR,
               _struct("N", ("s", STR)),
               _struct("M", ("i", I32), ("s", STR)),
               ArrayType(base_type=STR, size=2),
               DynamicArrayType(base_type=STR)):
        assert type_class_of(ty) is TypeClass.MOVE, ty


def test_move_types():
    owning_struct = _struct("W", ("items", DynamicArrayType(base_type=I32)))
    for ty in (DynamicArrayType(base_type=I32),
               GenericTypeRef(base_name="List", type_args=[I32]),
               GenericTypeRef(base_name="Own", type_args=[I32]),
               owning_struct,
               # A fixed array inherits move-ness from its elements.
               ArrayType(base_type=owning_struct, size=2)):
        assert type_class_of(ty) is TypeClass.MOVE, ty


def test_a_type_that_owns_two_kinds_of_heap_still_moves():
    """A struct holding BOTH a string and a dynamic array MOVES.

    This used to guard an ORDERING hazard: `type_class_of` asked "moves?" before "contains a
    string?", and asking in the other order would have classified this COPY and aliased the
    array buffer. There is only one question now, so the hazard is gone by construction --
    the case is kept because it is still the shape most likely to be got wrong.
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
    assert type_class_of(stringy) is TypeClass.MOVE   # was COPY before Phase 9


def test_unresolved_named_type_is_resolved_before_classifying():
    """An UnknownType answers PLAIN unless resolved -- which would alias an owning value.

    `owns_heap` returns False for an UnknownType by design ("Pass 2 rejects
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


def test_reference_classifies_as_its_referent():
    """A reference carries the type class of the value it names, in both modes.

    This row used to assert PLAIN for every reference, on the reading that "a borrow owns
    nothing". That conflated the two halves of the decision: the borrow is the PROVENANCE
    (BORROWED, which `_name_provenance` answers for a reference-typed name), and this
    function answers the OTHER half -- does the value own heap? Answering PLAIN here made
    (BORROWED, MOVE) unreachable through a reference, so `f(a)` on a `poke i32[]`
    parameter classified ADOPT in the checker and REJECT in the backend: #301's CE0129,
    #310's compile-clean double free, #311's ref-to-ref rebind.
    """
    owning = DynamicArrayType(base_type=I32)
    for mode in (BorrowMode.PEEK, BorrowMode.POKE):
        assert type_class_of(ReferenceType(referenced_type=owning,
                                           mutability=mode)) is TypeClass.MOVE
    for mode in (BorrowMode.PEEK, BorrowMode.POKE):
        assert type_class_of(ReferenceType(referenced_type=I32,
                                           mutability=mode)) is TypeClass.PLAIN


def test_reference_to_string_moves():
    """`string` owns heap at the type level, so a borrow of one classifies MOVE."""
    assert type_class_of(ReferenceType(referenced_type=STR,
                                       mutability=BorrowMode.PEEK)) is TypeClass.MOVE


def test_reference_referent_is_resolved_through_the_reference():
    """The resolver must reach the referent, not stop at the reference wrapper.

    An unresolved referent answers PLAIN, which would reclassify an owning value as owning
    nothing -- the same miss `test_a_resolver_that_misses_leaves_the_type_alone` guards for
    a bare name.
    """
    owning = _struct("W", ("items", DynamicArrayType(base_type=I32)))

    def resolve(ty):
        return owning if getattr(ty, "name", None) == "W" else ty

    ref = ReferenceType(referenced_type=UnknownType(name="W"), mutability=BorrowMode.POKE)
    assert type_class_of(ref, resolve) is TypeClass.MOVE


def test_consuming_a_reference_is_rejected():
    """The cell that follows: a borrow of an owning value cannot be given away.

    (BORROWED, MOVE) -> REJECT is CE2411, and `.clone()` is the escape. This is the whole
    reason the row above had to change.
    """
    owning = ReferenceType(referenced_type=DynamicArrayType(base_type=I32),
                           mutability=BorrowMode.POKE)
    assert classify(Provenance.BORROWED, type_class_of(owning)) is Ownership.REJECT


def test_consuming_a_reference_to_a_plain_value_adopts():
    """(BORROWED, PLAIN) stays ADOPT: copying a value that owns no heap transfers nothing.

    This is what keeps `fn f(peek i32 x)` usable -- passing `x` on by value is a copy of
    four bytes, not a transfer of an owner.
    """
    plain = ReferenceType(referenced_type=I32, mutability=BorrowMode.PEEK)
    assert classify(Provenance.BORROWED, type_class_of(plain)) is Ownership.ADOPT


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


# --- Get-out detection ---------------------------------------------------------------

def test_is_own_type():
    """`Own@(T)` alone, by name. One arm of `is_get_out_container` since #242."""
    assert is_own_type(GenericTypeRef(base_name="Own", type_args=[I32]))
    assert is_own_type(StructType(name="Own<i32>", fields=()))
    assert not is_own_type(GenericTypeRef(base_name="List", type_args=[I32]))
    assert not is_own_type(StructType(name="Owner", fields=()))  # prefix, not the type
    assert not is_own_type(None)


def test_is_get_out_container():
    """Every container keeps the element its `.get()` hands back, so every one is a view.

    Until #242 only `Own` was -- the others deep-copied at the read, and confusing the two
    was #256. Deleting the reader-side copies made all four the same question.

    Keyed on the receiver's TYPE, never on the method name: a user extension method called
    `get` returns a fresh value, and reading it as a container get-out would report a
    false CE2411.
    """
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


def test_consuming_use_set_is_the_documented_eleven():
    """The closed set. A twelfth position must be declared here before it can exist."""
    assert {u.name for u in ConsumingUse} == {
        "CALL_ARG", "LET", "REBIND", "FIELD_ASSIGN", "STRUCT_FIELD", "ENUM_PAYLOAD",
        "ARRAY_ELEMENT", "CONTAINER_INSERT", "RETURN", "CAPTURE", "OWN_ALLOC",
    }
