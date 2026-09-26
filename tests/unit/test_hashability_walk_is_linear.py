"""A type's hashability is decided once, however many fields reach it.

`can_struct_be_hashed` copied its visited set for each field, so the set could not stop
a re-visit across branches and the walk was exponential in the FAN-OUT. Twelve structs,
each with four fields of the type before it, cost 8 seconds; thirteen cost 28.5 (#598).
The `derive` pass then asked the same question a second time for every type, because
`register_*_hash_method` re-decided what its caller had just decided.

Counted rather than timed: a wall-clock budget is flaky on a loaded machine, and the
fault is the shape of the growth.
"""
from __future__ import annotations


from sushi_lang.semantics.generics import hashing












def _struct(name, fields):
    from sushi_lang.semantics.typesys import StructType
    return StructType(name=name, fields=tuple(fields))


def _point_at(struct_type, fields):
    """A frozen StructType's fields, rewritten to close a cycle for the test."""
    object.__setattr__(struct_type, "fields", tuple(fields))


def test_a_cycle_is_still_reported_with_its_path():
    a = _struct("A", [])
    b = _struct("B", [("back", a)])
    _point_at(a, [("fwd", b)])

    can_hash, reason = hashing.can_struct_be_hashed(a)
    assert not can_hash
    assert "recursive struct type: A -> B -> A" in reason


def test_a_cycle_verdict_is_not_remembered_for_the_type_it_passed_through():
    """The memo must hold nothing a cycle took part in.

    B is unhashable *while A is on the path*. Asked on its own, B has to walk again and
    answer about its own cycle -- a remembered verdict would refuse a type for a path
    that the reader is not standing on.
    """
    a = _struct("A", [])
    b = _struct("B", [("back", a)])
    _point_at(a, [("fwd", b)])

    hashing.can_struct_be_hashed(a)
    _can_hash, reason = hashing.can_struct_be_hashed(b)
    assert "recursive struct type: B -> A -> B" in reason


def test_a_type_reached_twice_through_two_fields_is_not_recursive():
    from sushi_lang.semantics.typesys import BuiltinType

    leaf = _struct("Leaf", [("v", BuiltinType.I32)])
    mid = _struct("Mid", [("a", leaf), ("b", leaf)])
    top = _struct("Top", [("l", mid), ("r", mid)])

    assert hashing.can_struct_be_hashed(top) == (True, "all fields are hashable")


def test_a_real_refusal_is_remembered_and_still_reported():
    from sushi_lang.semantics.typesys import ForeignPtrType

    bad = _struct("Bad", [("p", ForeignPtrType())])
    holder = _struct("Holder", [("x", bad), ("y", bad)])

    can_hash, reason = hashing.can_struct_be_hashed(holder)
    assert not can_hash
    assert "foreign ptr" in reason
