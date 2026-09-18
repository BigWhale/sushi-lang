"""A constraint naming no perk reads CE4003, called or not (#703).

"Does the perk exist" has to be asked before any instantiation asks "does the type
implement it", or the whole-program monomorphize stop (#579) answers first and names a
perk that is declared nowhere.
"""
from __future__ import annotations

UNCALLED = """
fn need@(T: Nothing)(T x) u64:
    return Result.Ok(0)

fn main() i32:
    return Result.Ok(0)
"""

CALLED = """
fn need@(T: Nothing)(T x) u64:
    return Result.Ok(0)

fn main() i32:
    let u64 n = need(5).realise(0)
    return Result.Ok(0)
"""

STRUCT_CALLED = """
struct Box@(T: Nothing):
    T item

fn main() i32:
    let Box@(i32) b = Box(5)
    return Result.Ok(0)
"""


def _codes(reporter):
    return [d.code for d in reporter.items if d.kind == "error"]


def test_an_uncalled_generic_answers_ce4003(analyze):
    assert _codes(analyze(UNCALLED)) == ["CE4003"]


def test_a_called_generic_answers_ce4003_too(analyze):
    """It used to answer CE4006, naming a perk that does not exist."""
    assert _codes(analyze(CALLED)) == ["CE4003"]


def test_an_instantiated_struct_answers_ce4003(analyze):
    assert _codes(analyze(STRUCT_CALLED)) == ["CE4003"]
