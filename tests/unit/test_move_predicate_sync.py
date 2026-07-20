"""#134: type_moves_by_value verdict table + move-implies-needs_cleanup sync."""
import pytest

from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
    ForeignPtrType, StructType, UnknownType, is_owning_type, type_moves_by_value,
)
from sushi_lang.backend.destructors import needs_cleanup

I32, STR = BuiltinType.I32, BuiltinType.STRING

def struct(name, *fields):
    return StructType(name=name, fields=tuple(fields))

PLAIN = struct("Plain", ("a", I32), ("b", I32))
STRING_ONLY = struct("Named", ("name", STR), ("id", I32))          # the string-distinction pin
OWNING = struct("Buffer", ("data", DynamicArrayType(I32)))
NESTED_OWNING = struct("Outer", ("inner", OWNING), ("tag", STR))
LIST_FIELD = struct("Bag", ("xs", struct("List<i32>", ("d", DynamicArrayType(I32)))))
PLAIN_ENUM = EnumType(name="Color", variants=(EnumVariantInfo("Red", ()), EnumVariantInfo("Green", ())))
OWNING_ENUM = EnumType(name="Msg", variants=(EnumVariantInfo("Text", (STR,)), EnumVariantInfo("Arr", (DynamicArrayType(I32),))))
STRING_ENUM = EnumType(name="Label", variants=(EnumVariantInfo("Named", (STR,)),))
RECURSIVE_ENUM = EnumType(name="Tree", variants=(EnumVariantInfo("Leaf", (I32,)), EnumVariantInfo("Node", (struct("Own<Tree>", ("p", I32)),))))

VERDICTS = [
    (I32, False), (STR, False), (ForeignPtrType(), False), (UnknownType("Pending"), False),
    (DynamicArrayType(I32), True),
    (PLAIN, False), (STRING_ONLY, False),
    (OWNING, True), (NESTED_OWNING, True), (LIST_FIELD, True),
    (PLAIN_ENUM, False), (STRING_ENUM, False), (OWNING_ENUM, True), (RECURSIVE_ENUM, True),
    (ArrayType(OWNING, 3), True), (ArrayType(I32, 3), False), (ArrayType(STRING_ONLY, 2), False),
]

@pytest.mark.parametrize("t,expected", VERDICTS, ids=[str(t) for t, _ in VERDICTS])
def test_verdict(t, expected):
    assert type_moves_by_value(t) is expected

def test_delegates_base_cases_to_is_owning_type():
    for t in (DynamicArrayType(I32), struct("Own<i32>", ("p", I32)), struct("List<i32>", ("d", DynamicArrayType(I32)))):
        assert is_owning_type(t) and type_moves_by_value(t)

def test_move_implies_needs_cleanup():
    # move => something to free; converse deliberately false (strings).
    for t, moves in VERDICTS:
        if moves:
            assert needs_cleanup(t), f"{t} moves but needs_cleanup says nothing to free"
    assert needs_cleanup(STRING_ONLY) and not type_moves_by_value(STRING_ONLY)

def test_self_recursive_struct_terminates():
    # Cycle guard: a struct whose field names its own type (post-mono shells can self-refer).
    rec = struct("SelfRef", ("next", UnknownType("SelfRef")), ("n", I32))
    assert type_moves_by_value(rec) is False
