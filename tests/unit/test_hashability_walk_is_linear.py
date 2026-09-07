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

import collections

from sushi_lang.semantics.generics import hashing


DEPTH = 10
FAN = 4


def _fanning_structs(depth: int, fan: int) -> str:
    lines = ["struct S0:", "    i32 a", ""]
    for i in range(1, depth):
        lines.append(f"struct S{i}:")
        lines += [f"    S{i - 1} f{k}" for k in range(fan)]
        lines.append("")
    lines += ["fn main() i32:", '    println("Mostly Harmless")', "    return Result.Ok(0)"]
    return "\n".join(lines)


def _visit_counts(analyze, monkeypatch, src: str) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    original = hashing.can_struct_be_hashed

    def counted(struct_type, *args, **kwargs):
        counts[struct_type.name] += 1
        return original(struct_type, *args, **kwargs)

    monkeypatch.setattr(hashing, "can_struct_be_hashed", counted)
    monkeypatch.setattr("sushi_lang.semantics.passes.derive.can_struct_be_hashed", counted)
    analyze(src)
    return counts


def test_a_fanning_struct_graph_is_not_walked_exponentially(analyze, monkeypatch):
    counts = _visit_counts(analyze, monkeypatch, _fanning_structs(DEPTH, FAN))
    total = sum(counts.values())
    assert total <= 20 * DEPTH, (
        f"{total} hashability decisions for {DEPTH} structs of fan-out {FAN}. A visited "
        "set copied per field cannot stop a re-visit across branches, so the walk "
        "multiplies by the fan-out at every link."
    )


def test_a_hashable_struct_graph_is_still_hashable(analyze):
    reporter = analyze(_fanning_structs(4, 2))
    assert not [item for item in reporter.items if item.severity.name == "ERROR"]


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
