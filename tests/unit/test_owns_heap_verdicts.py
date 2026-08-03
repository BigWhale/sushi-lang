"""The `owns_heap` verdict table, type by type.

**This file used to be `test_move_predicate_sync.py`, and its job was to stop two predicates
drifting.** `type_moves_by_value` (semantics) and `needs_cleanup` (backend) each answered
"does this own heap?", and it asserted `move => needs_cleanup` with the converse deliberately
false -- the exception being exactly `string`, which needed freeing but did not move. That one
exception WAS the COPY tier.

Phase 9 made a string move, so the two answers became one answer, `needs_cleanup` became a
thin alias of `owns_heap`, and a sync test between them has nothing left to compare. What
survives is the part that was always worth having: the verdict table.

**Four verdicts flipped when the tier went**, and they are the whole point of the phase:
`string`, a string-only struct, a string-carrying enum, and a fixed array of string-only
structs all now own heap. Everything else is unchanged.
"""
import pytest

from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    ForeignPtrType, StructType, UnknownType, owns_heap,
)
from sushi_lang.backend.destructors import needs_cleanup

I32, STR = BuiltinType.I32, BuiltinType.STRING


def struct(name, *fields):
    return StructType(name=name, fields=tuple(fields))


PLAIN = struct("Plain", ("a", I32), ("b", I32))
STRING_ONLY = struct("Named", ("name", STR), ("id", I32))          # the tier that was deleted
OWNING = struct("Buffer", ("data", DynamicArrayType(I32)))
NESTED_OWNING = struct("Outer", ("inner", OWNING), ("tag", STR))
LIST_FIELD = struct("Bag", ("xs", struct("List<i32>", ("d", DynamicArrayType(I32)))))
HASHMAP = struct("HashMap<i32, i32>", ("buckets", DynamicArrayType(I32)))
PLAIN_ENUM = EnumType(name="Color", variants=(EnumVariantInfo("Red", ()), EnumVariantInfo("Green", ())))
OWNING_ENUM = EnumType(name="Msg", variants=(EnumVariantInfo("Text", (STR,)), EnumVariantInfo("Arr", (DynamicArrayType(I32),))))
STRING_ENUM = EnumType(name="Label", variants=(EnumVariantInfo("Named", (STR,)),))
RECURSIVE_ENUM = EnumType(name="Tree", variants=(EnumVariantInfo("Leaf", (I32,)), EnumVariantInfo("Node", (struct("Own<Tree>", ("p", I32)),))))

# (type, owns heap?). The four marked FLIPPED answered False before Phase 9.
VERDICTS = [
    (I32, False),
    (STR, True),                        # FLIPPED -- a string owns its buffer
    (ForeignPtrType(), False),
    (UnknownType("Pending"), False),
    (DynamicArrayType(I32), True),
    (PLAIN, False),
    (STRING_ONLY, True),                # FLIPPED -- the string-only struct tier is gone
    (OWNING, True),
    (NESTED_OWNING, True),
    (LIST_FIELD, True),
    (HASHMAP, True),
    (PLAIN_ENUM, False),
    (STRING_ENUM, True),                # FLIPPED
    (OWNING_ENUM, True),
    (RECURSIVE_ENUM, True),
    (ArrayType(OWNING, 3), True),
    (ArrayType(I32, 3), False),
    (ArrayType(STRING_ONLY, 2), True),  # FLIPPED
]


@pytest.mark.parametrize("t,expected", VERDICTS, ids=[str(t) for t, _ in VERDICTS])
def test_verdict(t, expected):
    assert owns_heap(t) is expected


def test_needs_cleanup_is_an_alias_not_a_second_answer():
    """The backend's question and the semantics question are now literally one function.

    This is what `test_move_predicate_sync.py` was trying to protect by hand. If someone
    re-implements `needs_cleanup` with its own arms, this goes red on the first type they
    disagree about -- which historically was `string`.
    """
    for t, expected in VERDICTS:
        assert needs_cleanup(t) is expected, f"{t}: needs_cleanup disagrees with owns_heap"


def test_the_deleted_tier_is_really_gone():
    """A string-only composite MOVES now. Stated on its own so the intent is greppable.

    Before Phase 9 each of these was a COPY: the compiler duplicated it at a sink and the
    source stayed usable. `.clone()` is the only way to get that now.
    """
    assert owns_heap(STR) is True
    assert owns_heap(STRING_ONLY) is True
    assert owns_heap(STRING_ENUM) is True
    assert owns_heap(ArrayType(STRING_ONLY, 2)) is True


def test_self_recursive_struct_terminates():
    # Cycle guard: a struct whose field names its own type (post-mono shells can self-refer).
    rec = struct("SelfRef", ("next", UnknownType("SelfRef")), ("n", I32))
    assert owns_heap(rec) is False
