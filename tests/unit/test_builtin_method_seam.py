"""The built-in-method seam and the typecheck pass must recognise the same families.

`builtin_method_exists` (CE2097) and the family table in
`semantics/passes/types/method_registry.py` answer one question: is this name a
compiler-defined method on this receiver type. This file holds the two answers equal over
a MATRIX of receivers and names, and reads each family's own name table to build the
names. A family that one side knows and the other does not makes a cell disagree.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.derived_methods import DerivedMethodTable
from sushi_lang.semantics.generics.builtin_methods import builtin_method_exists
from sushi_lang.semantics.generics.hashmap import HASHMAP_METHOD_ARITY
from sushi_lang.semantics.generics.list import LIST_METHOD_ARITY
from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_ARITY
from sushi_lang.semantics.generics.own import OWN_METHOD_ARITY
from sushi_lang.semantics.generics.primitives import PRIMITIVE_METHOD_RETURNS
from sushi_lang.semantics.generics.results import RESULT_METHOD_ARITY
from sushi_lang.semantics.passes.types.arrays import _ARRAY_METHODS
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    FunctionType,
    ReferenceType,
    StructType,
)
from sushi_lang.sushi_stdlib.src.collections.strings import METHOD_SPECS


class _Derived:
    """A stand-in for a derived method: the table keys it by its name."""

    def __init__(self, name: str) -> None:
        self.name = name


def _struct(name: str, base: str | None = None) -> StructType:
    return StructType(name=name, fields=(), generic_base=base)


def _enum(name: str, base: str | None = None) -> EnumType:
    return EnumType(name=name, variants=(), generic_base=base)


_POINT = _struct("Point")
_BARE = _struct("Bare")
_COLOUR = _enum("Colour")
_PLAIN = _enum("Plain")
_LIST = _struct("List<i32>", "List")
_HASHMAP = _struct("HashMap<i32, string>", "HashMap")
_OWN = _struct("Own<i32>", "Own")
_RESULT = _enum("Result<i32, StdError>", "Result")
_MAYBE = _enum("Maybe<i32>", "Maybe")


def _derived_table() -> DerivedMethodTable:
    """What the `derive` pass writes: hash and clone on the user types that derive them,
    hash on the containers that hash what they hold, and nothing on `Bare`/`Plain`."""
    table = DerivedMethodTable()
    for ty in (_POINT, _COLOUR, _RESULT, _MAYBE):
        table.register_method(ty, _Derived("hash"))
        table.register_method(ty, _Derived("clone"))
    for ty in (_LIST, _OWN):
        table.register_method(ty, _Derived("hash"))
    return table


DERIVED = _derived_table()


class _NoPerks:
    """No perk implementation: the claim and the seam then ask the same question."""

    def get_method(self, target_type, method_name):
        return None


class _Validator:
    perk_impl_table = _NoPerks()
    derived_methods = DERIVED


#: Every built-in family, a user struct and enum with and without a derived method, a
#: reference, and an array of each kind.
RECEIVERS = (
    DynamicArrayType(BuiltinType.I32),
    ArrayType(BuiltinType.I32, 3),
    BuiltinType.STRING,
    BuiltinType.I32,
    BuiltinType.U8,
    BuiltinType.F32,
    BuiltinType.F64,
    BuiltinType.BOOL,
    _RESULT, _MAYBE, _OWN, _HASHMAP, _LIST,
    _POINT, _BARE, _COLOUR, _PLAIN,
    FunctionType(param_types=(BuiltinType.I32,), ok_type=BuiltinType.I32,
                 err_type=BuiltinType.I32),
    ReferenceType(referenced_type=DynamicArrayType(BuiltinType.I32)),
    ReferenceType(referenced_type=_POINT),
    ReferenceType(referenced_type=_LIST),
)

#: Each family's own names, read from the family's own table, plus a miss.
NAMES = tuple(sorted(
    set(_ARRAY_METHODS) | set(METHOD_SPECS) | {"is_empty", "clone", "hash"}
    | set(RESULT_METHOD_ARITY) | set(MAYBE_METHOD_ARITY) | set(OWN_METHOD_ARITY)
    | set(HASHMAP_METHOD_ARITY) | set(LIST_METHOD_ARITY) | set(PRIMITIVE_METHOD_RETURNS)
    | {"no_such_method"}))


def _deref(receiver):
    if isinstance(receiver, ReferenceType):
        return receiver.referenced_type
    return receiver


def _claimed(receiver, name):
    """The families of the table that claim this pair. Both callers of the table deref a
    borrow before they ask, so the matrix does the same."""
    return [family.name for family in METHOD_TYPE_REGISTRY.families
            if family.claims(_deref(receiver), name, _Validator())]


def test_the_seam_and_the_family_table_agree_on_every_cell():
    """One question, one answer: the seam says yes exactly where a family claims."""
    disagree = []
    for receiver in RECEIVERS:
        for name in NAMES:
            seam = builtin_method_exists(receiver, name, DERIVED)
            table = bool(_claimed(receiver, name))
            if seam != table:
                disagree.append(f"{receiver}.{name}(): seam={seam} table={table}")
    assert not disagree, (
        "builtin_method_exists and METHOD_TYPE_REGISTRY disagree:\n  "
        + "\n  ".join(disagree))


def test_the_matrix_reaches_every_family():
    """The always-fires control: a family no cell reaches is a family the check above
    asserted nothing about."""
    seen = set()
    for receiver in RECEIVERS:
        for name in NAMES:
            seen.update(_claimed(receiver, name))
    unreached = sorted({family.name for family in METHOD_TYPE_REGISTRY.families} - seen)
    assert not unreached, "the matrix never reaches: " + ", ".join(unreached)


def test_the_matrix_holds_misses_as_well_as_hits():
    """A matrix of hits alone cannot see a seam that says yes to everything."""
    answers = {builtin_method_exists(r, n, DERIVED) for r in RECEIVERS for n in NAMES}
    assert answers == {True, False}


def test_a_perk_override_is_still_a_builtin_to_the_seam():
    """A perk implementation is the sanctioned override: the TABLE yields to it, so the
    call goes to the perk, but the name stays a compiler-defined method, and an
    extension of that name is still CE2097."""

    class _PerkOnEverything:
        def get_method(self, target_type, method_name):
            return object()

    class _PerkValidator(_Validator):
        perk_impl_table = _PerkOnEverything()

    for receiver, name in ((BuiltinType.I32, "to_str"), (_POINT, "hash"),
                           (_POINT, "clone")):
        claimed = [family.name for family in METHOD_TYPE_REGISTRY.families
                   if family.claims(receiver, name, _PerkValidator())]
        assert claimed == []
        assert builtin_method_exists(receiver, name, DERIVED) is True


# Behaviour, per family

def _list(elem="i32"):
    return StructType(name=f"List<{elem}>", fields=(), generic_base="List")


# The auto-derived pair is one COMPILATION's (#601); every other family is the
# compiler's, so an empty table is the right stand-in for those cases.
NOTHING_DERIVED = DerivedMethodTable()


@pytest.mark.parametrize("receiver,method", [
    (DynamicArrayType(base_type=BuiltinType.I32), "len"),
    (ArrayType(base_type=BuiltinType.I32, size=3), "get"),
    (BuiltinType.STRING, "trim"),
    (BuiltinType.STRING, "to_str"),
    (BuiltinType.STRING, "hash"),
    (BuiltinType.I32, "to_str"),
    (BuiltinType.I32, "hash"),
    (BuiltinType.F64, "to_bits"),
    (EnumType(name="Result<i32, StdError>", variants=(), generic_base="Result"), "is_ok"),
    (EnumType(name="Maybe<i32>", variants=(), generic_base="Maybe"), "is_some"),
    (StructType(name="Own<i32>", fields=(), generic_base="Own"), "get"),
    (StructType(name="HashMap<i32, i32>", fields=(), generic_base="HashMap"), "insert"),
    (_list(), "push"),
])
def test_recognised(receiver, method):
    assert builtin_method_exists(receiver, method, NOTHING_DERIVED) is True


@pytest.mark.parametrize("receiver,method", [
    (DynamicArrayType(base_type=BuiltinType.I32), "squared"),
    (BuiltinType.STRING, "shout"),
    (BuiltinType.I32, "squared"),
    (BuiltinType.I32, "to_bits"),          # float-only
    (BuiltinType.BOOL, "to_bits"),
    (_list(), "sum_all"),                  # the real extension in tests/bugs
    (StructType(name="Point", fields=()), "describe"),
    # EVERY File method is an ordinary extension or perk implementation in <io/fs> now,
    # so the seam must claim none of them -- claiming one would make CE2097 reject the
    # real definition (HANDLES.md Phase 5). `lines()` was the last builtin and Phase 7d
    # removed it: line iteration is BufReader's, walked by the `next()` protocol.
    (StructType(name="File", fields=()), "write"),
    (StructType(name="File", fields=()), "close"),
    (StructType(name="File", fields=()), "lines"),
    (None, "hash"),
])
def test_not_recognised(receiver, method):
    assert builtin_method_exists(receiver, method, NOTHING_DERIVED) is False


def test_reference_receivers_unwrap():
    """Methods on &T are the methods on T, so a borrow must not hide a collision."""
    arr = DynamicArrayType(base_type=BuiltinType.I32)
    assert builtin_method_exists(ReferenceType(referenced_type=arr), "len",
                                 NOTHING_DERIVED)


