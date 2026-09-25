"""CE2035 is reported ONCE per hole that cannot be printed (#885).

An inference visitor answers a type and reports nothing, because inference runs more
than once over one expression. The check is in the validation of an interpolated
string, which visits each hole once.
"""
from __future__ import annotations


ONE_HOLE = """fn main() i32:
    let u32[] a = from([1 as u32])
    println("interp {a.get(0)}")
    return Result.Ok(0)
"""

TWO_HOLES = """fn main() i32:
    let u32[] a = from([1 as u32])
    let string s = "{a.get(0)} and {a.first()}"
    println(s)
    return Result.Ok(0)
"""

PRINTABLE = """fn main() i32:
    let u32[] a = from([1 as u32])
    println("interp {a.get(0).realise(0 as u32)}")
    return Result.Ok(0)
"""


def refusals(reporter):
    return [d for d in reporter.items if d.code == "CE2035"]


def test_one_hole_is_one_diagnostic(analyze):
    found = refusals(analyze(ONE_HOLE))
    assert len(found) == 1, [(d.span, d.message) for d in found]
    assert found[0].span.line == 3


def test_each_hole_is_its_own_diagnostic(analyze):
    found = refusals(analyze(TWO_HOLES))
    assert sorted((d.span.line, d.span.col) for d in found) == [(3, 22), (3, 37)]


def test_a_printable_hole_is_not_refused(analyze):
    assert refusals(analyze(PRINTABLE)) == []
