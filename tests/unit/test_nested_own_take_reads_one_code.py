"""A `nom` binding nested inside an `Own(...)` pattern reads CE2434, and only CE2434 (#783).

The borrow pass recursed into the nested pattern without the scrutinee's ownership, so
the binding read CE2432 ("this match only borrows its scrutinee") under `match nom r:`
and under a temporary, where the match OWNS the scrutinee. The fault is the Own position:
the heap cell would have nothing to free it. `EXPECT_ERROR_CODE` matches a substring, so
the code set is pinned here.
"""
from __future__ import annotations

import pytest

_TYPES = (
    "enum Inner:\n    Word(string)\n    Nil\n\n"
    "enum Outer:\n    Full(Own@(Inner))\n    Empty\n\n"
    "fn make() Outer:\n"
    "    return Result.Ok(Outer.Full(Own.alloc(Inner.Word(\"x\".clone()))))\n\n"
)


def _program(scrutinee: str, pattern: str) -> str:
    return (_TYPES
            + "fn main() i32:\n"
            + "    let string kept = \"none\".clone()\n"
            + "    let Outer r = make().realise(Outer.Empty)\n"
            + f"    match {scrutinee}:\n"
            + f"        Outer.Full({pattern}) ->\n"
            + "            kept := s\n"
            + "        Outer.Full(_) -> println(\"other\")\n"
            + "        Outer.Empty -> println(\"empty\")\n"
            + "    println(kept)\n"
            + "    return Result.Ok(0)\n")


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items if item.code.startswith("CE")]


@pytest.mark.parametrize("scrutinee", ["nom r", "r", "make().realise(Outer.Empty)"])
def test_a_nested_take_inside_own_reads_ce2434(analyze, scrutinee):
    source = _program(scrutinee, "Own(Inner.Word(nom s))")
    assert _codes(analyze(source, name="m")) == ["CE2434"]


@pytest.mark.parametrize("scrutinee", ["nom r", "make().realise(Outer.Empty)"])
def test_a_nested_bare_binding_inside_own_reads_nothing(analyze, scrutinee):
    source = _program(scrutinee, "Own(Inner.Word(s))").replace(
        "            kept := s\n", "            println(s)\n")
    assert _codes(analyze(source, name="m")) == []
