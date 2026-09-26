"""The `owns_resource` verdict table, type by type.

Every type here answers by STRUCTURE. The declared half -- a type that implements
`Drop` -- has its own gate, `tests/unit/test_cleanup_predicates_agree.py`, because the
question there is whether three layers give one answer rather than what the answer is.
"""
import pytest

from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    ForeignPtrType, StructType, UnknownType, owns_resource,
)



I32, STR = BuiltinType.I32, BuiltinType.STRING

# No type in this table declares a resource; `owns_resource` still requires the answer.
NO_DROPS: frozenset = frozenset()


def struct(name, *fields, base=None):
    return StructType(name=name, fields=tuple(fields), generic_base=base)


PLAIN = struct("Plain", ("a", I32), ("b", I32))
STRING_ONLY = struct("Named", ("name", STR), ("id", I32))          # the tier that was deleted
OWNING = struct("Buffer", ("data", DynamicArrayType(I32)))
NESTED_OWNING = struct("Outer", ("inner", OWNING), ("tag", STR))
LIST_FIELD = struct("Bag", ("xs", struct("List<i32>", ("d", DynamicArrayType(I32)), base="List")))
HASHMAP = struct("HashMap<i32, i32>", ("buckets", DynamicArrayType(I32)), base="HashMap")
PLAIN_ENUM = EnumType(name="Color", variants=(EnumVariantInfo("Red", ()), EnumVariantInfo("Green", ())))
OWNING_ENUM = EnumType(name="Msg", variants=(EnumVariantInfo("Text", (STR,)), EnumVariantInfo("Arr", (DynamicArrayType(I32),))))
STRING_ENUM = EnumType(name="Label", variants=(EnumVariantInfo("Named", (STR,)),))
RECURSIVE_ENUM = EnumType(name="Tree", variants=(EnumVariantInfo("Leaf", (I32,)), EnumVariantInfo("Node", (struct("Own<Tree>", ("p", I32), base="Own"),))))

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
    assert owns_resource(t, NO_DROPS) is expected




def test_the_deleted_tier_is_really_gone():
    """A string-only composite MOVES now. Stated on its own so the intent is greppable."""
    assert owns_resource(STR, NO_DROPS) is True
    assert owns_resource(STRING_ONLY, NO_DROPS) is True
    assert owns_resource(STRING_ENUM, NO_DROPS) is True
    assert owns_resource(ArrayType(STRING_ONLY, 2), NO_DROPS) is True


def test_self_recursive_struct_terminates():
    # Cycle guard: a struct whose field names its own type (post-mono shells can self-refer).
    rec = struct("SelfRef", ("next", UnknownType("SelfRef")), ("n", I32))
    assert owns_resource(rec, NO_DROPS) is False
