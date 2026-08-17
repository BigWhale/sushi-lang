"""The `owns_heap` verdict table, type by type."""
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
    """The backend's question and the semantics question are now literally one function."""
    for t, expected in VERDICTS:
        assert needs_cleanup(t) is expected, f"{t}: needs_cleanup disagrees with owns_heap"


def test_the_deleted_tier_is_really_gone():
    """A string-only composite MOVES now. Stated on its own so the intent is greppable."""
    assert owns_heap(STR) is True
    assert owns_heap(STRING_ONLY) is True
    assert owns_heap(STRING_ENUM) is True
    assert owns_heap(ArrayType(STRING_ONLY, 2)) is True


def test_self_recursive_struct_terminates():
    # Cycle guard: a struct whose field names its own type (post-mono shells can self-refer).
    rec = struct("SelfRef", ("next", UnknownType("SelfRef")), ("n", I32))
    assert owns_heap(rec) is False
