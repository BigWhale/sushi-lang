"""CE0140 is reported ONCE per block, at the first dead statement (#854).

A block with three statements after its `return` is one fault, not three. A second
block with dead code is a second fault. The note points at the statement that ends
the path.
"""
from __future__ import annotations


THREE_DEAD_IN_ONE_BLOCK = """fn f(i32 x) i32:
    return Result.Ok(x)
    println("one")
    println("two")
    return Result.Ok(2)

fn main() i32:
    println("{f(1).realise(0)}")
    return Result.Ok(0)
"""

TWO_BLOCKS = """fn g(i32 x) i32:
    while (x > 0):
        break
        println("loop one")
        println("loop two")
    return Result.Ok(x)
    println("after")
    println("after again")

fn main() i32:
    println("{g(1).realise(0)}")
    return Result.Ok(0)
"""


def dead(reporter):
    return [d for d in reporter.items if d.code == "CE0140"]


def test_one_block_is_one_diagnostic(analyze):
    found = dead(analyze(THREE_DEAD_IN_ONE_BLOCK))
    assert len(found) == 1
    assert found[0].span.line == 3


def test_the_note_is_at_the_statement_that_ends_the_path(analyze):
    (diagnostic,) = dead(analyze(THREE_DEAD_IN_ONE_BLOCK))
    notes = [s for s in diagnostic.sub if s.kind == "note"]
    assert len(notes) == 1
    assert notes[0].span is not None and notes[0].span.line == 2


def test_each_block_is_its_own_diagnostic(analyze):
    found = dead(analyze(TWO_BLOCKS))
    assert sorted(d.span.line for d in found) == [4, 7]
