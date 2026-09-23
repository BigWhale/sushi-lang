"""A wrong number of type arguments is one fault, and it reads one diagnostic (#796).

A written type (`let Box@(i32, i32) b`) answered CE2001 "unknown type" twice, once with no
location, then a cascade. An extension target with too many parameters reached an
internal error (CE0096), and a concrete one or a perk implementation was accepted in
silence. The one rule is CE2062: the generic takes N type arguments, and the position
gives M. The type positions name the declaration in a note.

`EXPECT_ERROR_CODE` matches a SUBSTRING, so no fixture can count. This module counts.
"""
from __future__ import annotations

import pytest

_BOX = "struct Box@(T):\n    T v\n\n"
_PAIR = "struct Pair@(T, U):\n    T a\n    U b\n\n"
_OPT = "enum Opt@(T):\n    Some(T)\n    Nothing\n\n"
_NAMED = "perk Named:\n    fn name() string\n\n"
_MAIN = "fn main() i32:\n    return Result.Ok(0)\n"

_TYPE_POSITIONS = {
    "let": _BOX + "fn main() i32:\n    let Box@(i32, i32) b = Box(1)\n"
                  "    println(b.v)\n    return Result.Ok(0)\n",
    "parameter": _BOX + "fn take(Box@(i32, i32) b) i32:\n    return Result.Ok(b.v)\n\n"
                        + _MAIN,
    "return": _BOX + "fn make() Box@(i32, i32):\n    return Result.Ok(Box(1))\n\n" + _MAIN,
    "field": _BOX + "struct Holder:\n    Box@(i32, i32) b\n\n" + _MAIN,
    "payload": _BOX + "enum Slot:\n    Full(Box@(i32, i32))\n    Empty\n\n" + _MAIN,
    "enum": _OPT + "fn main() i32:\n    let Opt@(i32, i32) o = Opt.Nothing\n"
                   "    match o:\n        Opt.Some(_) -> println(\"some\")\n"
                   "        Opt.Nothing -> println(\"none\")\n    return Result.Ok(0)\n",
    "too few": _PAIR + "fn main() i32:\n    let Pair@(i32) p = Pair(1, 2)\n"
                       "    println(p.a)\n    return Result.Ok(0)\n",
    "nested": _BOX + "fn main() i32:\n    let Maybe@(Box@(i32, i32)) m = Maybe.None\n"
                     "    println(m.is_some())\n    return Result.Ok(0)\n",
    "list": "fn main() i32:\n    let List@(i32, i32) xs = List.new()\n"
            "    println(xs.len())\n    return Result.Ok(0)\n",
}

_TARGET_POSITIONS = {
    "extension template": _BOX + "extend Box@(T, U) twice() i32:\n    return 2\n\n" + _MAIN,
    "extension concrete": _BOX + "extend Box@(i32, i32) twice() i32:\n    return 2\n\n"
                          + _MAIN,
    "perk implementation": _NAMED + _BOX + "extend Box@(T, U) with Named:\n"
                           "    fn name() string:\n        return \"box\"\n\n" + _MAIN,
}


def _errors(reporter):
    return [item for item in reporter.items if item.code.startswith("CE")]


@pytest.mark.parametrize("position", sorted(_TYPE_POSITIONS))
def test_a_written_type_reads_one_located_relational_diagnostic(analyze, position):
    errors = _errors(analyze(_TYPE_POSITIONS[position], name="m"))
    assert [e.code for e in errors] == ["CE2062"], [(e.code, e.message) for e in errors]
    (error,) = errors
    assert error.span is not None, error.message
    if position != "list":
        # A built-in generic has no declaration in the program to point at.
        assert any(sub.span is not None for sub in error.sub), error.sub


@pytest.mark.parametrize("position", sorted(_TARGET_POSITIONS))
def test_an_extension_target_reads_one_located_relational_diagnostic(analyze, position):
    errors = _errors(analyze(_TARGET_POSITIONS[position], name="m"))
    assert [e.code for e in errors] == ["CE2062"], [(e.code, e.message) for e in errors]
    (error,) = errors
    assert error.span is not None, error.message
    assert any(sub.span is not None for sub in error.sub), error.sub


def test_the_explicit_call_reads_the_same_code(analyze):
    source = ("fn id@(T)(T x) i32:\n    return Result.Ok(0)\n\n"
              "fn main() i32:\n    println(id@(i32, i32)(1).realise(0))\n"
              "    return Result.Ok(0)\n")
    assert [e.code for e in _errors(analyze(source, name="m"))] == ["CE2062"]


def test_a_right_count_reads_nothing(analyze):
    """The always-fires control's other half: the rule must not fire on a correct count."""
    source = (_PAIR + "fn main() i32:\n    let Pair@(i32, string) p = Pair(1, \"a\")\n"
              "    println(p.a)\n    return Result.Ok(0)\n")
    assert _errors(analyze(source, name="m")) == []
