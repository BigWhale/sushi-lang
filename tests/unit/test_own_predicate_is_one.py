"""`Own@(T)` is asked about in one place, and every spelling gets one answer.

Three predicates answered "is this an `Own@(T)`" and they disagreed (#680): one demanded
a `StructType` before the name test, one read the name off any type, and the public one
also stripped a reference. So one spelling was an `Own` to one reader and not to another.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, ReferenceType, StructType, UnknownType,
)


OWN_SPELLINGS = [
    StructType(name="Own<i32>", fields=()),
    GenericTypeRef(base_name="Own", type_args=(BuiltinType.I32,)),
    UnknownType(name="Own<i32>"),
]


def test_every_spelling_of_own_gets_one_answer():
    for spelling in OWN_SPELLINGS:
        assert is_own_type(spelling), f"{spelling!r} spells Own@(T) and was refused"
        for mode in (BorrowMode.PEEK, BorrowMode.POKE):
            borrowed = ReferenceType(referenced_type=spelling, mutability=mode)
            assert is_own_type(borrowed), f"a {mode} borrow of {spelling!r} was refused"


def test_what_is_not_an_own_stays_refused():
    assert not is_own_type(None)
    assert not is_own_type(BuiltinType.I32)
    assert not is_own_type(StructType(name="Owner", fields=()))
    assert not is_own_type(GenericTypeRef(base_name="List", type_args=(BuiltinType.I32,)))
    assert not is_own_type(UnknownType(name="Ownership"))


def test_the_typecheck_pass_keeps_no_predicate_of_its_own():
    from sushi_lang.semantics.passes.types import statements

    assert not hasattr(statements, "_is_own_type"), (
        "passes/types/statements.py answers the question itself again"
    )


def test_the_match_rule_reads_the_seam():
    from sushi_lang.semantics.passes.types import matching

    source = inspect.getsource(matching)
    assert 'startswith("Own<")' not in source, (
        "passes/types/matching.py tests the interned prefix by hand again"
    )
    assert "is_own_type(" in source, "the Own pattern rule must read the seam"


def test_the_backend_reads_the_seam():
    from sushi_lang.backend.memory.dynamic_arrays import DynamicArrayManager

    assert not hasattr(DynamicArrayManager, "is_own_type"), (
        "the back end keeps a fourth answer to the same question"
    )


def test_the_payload_is_read_the_same_way_from_either_spelling():
    """One reader for `Own@(T)`'s `T`: the pattern rule asks twice, and must agree."""
    from sushi_lang.semantics.generics.own import own_payload_type
    from sushi_lang.semantics.typesys import PointerType

    interned = StructType(
        name="Own<i32>", fields=(("value", PointerType(pointee_type=BuiltinType.I32)),))
    written = GenericTypeRef(base_name="Own", type_args=(BuiltinType.I32,))

    assert own_payload_type(interned) is BuiltinType.I32
    assert own_payload_type(written) is BuiltinType.I32
    assert own_payload_type(
        ReferenceType(referenced_type=interned, mutability=BorrowMode.POKE)
    ) is BuiltinType.I32


def test_a_type_that_is_not_an_own_has_no_payload():
    from sushi_lang.semantics.generics.own import own_payload_type

    assert own_payload_type(StructType(name="Owner", fields=())) is None
    assert own_payload_type(BuiltinType.I32) is None
    assert own_payload_type(
        GenericTypeRef(base_name="Own", type_args=(BuiltinType.I32, BuiltinType.I32))
    ) is None
