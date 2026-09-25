"""CE4007 on a perk implementation over a generic target is reported once (#861).

The name-conflict check is part of the header of the implementation, and the header of
a perk implementation on a generic target is judged once, on the written template. The
check read every monomorphized copy, so one conflict the source wrote once reported
CE4007 once for each instantiation, and a template with no instantiation reported none.

`EXPECT_ERROR_CODE` matches a SUBSTRING of the whole output, so no fixture can pin the
count. This module counts the codes.
"""
from __future__ import annotations

import pytest

_HEAD = "perk Sz:\n    fn sz() i32\n\nstruct Box@(T):\n    T v\n\n"

_IMPL = "extend Box@(T) with Sz:\n    fn sz() i32:\n        return 2\n\n"

_TEMPLATE_EXT = "extend Box@(T) sz() i32:\n    return 1\n\n"

_I32_EXT = "extend Box@(i32) sz() i32:\n    return 1\n\n"

_TWO = (
    "fn main() i32:\n"
    "    let Box@(i32) a = Box(1)\n"
    "    let Box@(string) b = Box(\"x\")\n"
    "    println(\"{a.v} {b.v}\")\n"
    "    return Result.Ok(0)\n"
)

_THREE = (
    "fn main() i32:\n"
    "    let Box@(i32) a = Box(1)\n"
    "    let Box@(string) b = Box(\"x\")\n"
    "    let Box@(bool) c = Box(true)\n"
    "    println(\"{a.v} {b.v} {c.v}\")\n"
    "    return Result.Ok(0)\n"
)

_ONE = (
    "fn main() i32:\n"
    "    let Box@(i32) a = Box(1)\n"
    "    println(\"{a.v}\")\n"
    "    return Result.Ok(0)\n"
)

_NONE = (
    "fn main() i32:\n"
    "    return Result.Ok(0)\n"
)


def _codes(reporter, code: str) -> list[str]:
    return [item.code for item in reporter.items if item.code == code]


@pytest.mark.parametrize("main", [_TWO, _THREE, _ONE, _NONE],
                         ids=["two_instances", "three_instances", "one_instance",
                              "no_instance"])
def test_a_conflict_on_a_generic_target_is_reported_once(analyze, main):
    reporter = analyze(_HEAD + _TEMPLATE_EXT + _IMPL + main, name="m")
    assert _codes(reporter, "CE4007") == ["CE4007"], [item.code for item in reporter.items]


@pytest.mark.parametrize("main", [_TWO, _NONE], ids=["two_instances", "no_instance"])
def test_a_concrete_extension_under_a_template_implementation_conflicts_once(analyze, main):
    """The template covers `Box@(i32)`, so the name has two homes there."""
    reporter = analyze(_HEAD + _I32_EXT + _IMPL + main, name="m")
    assert _codes(reporter, "CE4007") == ["CE4007"], [item.code for item in reporter.items]


def test_the_conflict_carries_a_note_at_the_extension(analyze):
    reporter = analyze(_HEAD + _TEMPLATE_EXT + _IMPL + _TWO, name="m")
    [diag] = [item for item in reporter.items if item.code == "CE4007"]
    notes = [sub for sub in diag.sub if sub.kind == "note" and sub.span is not None]
    assert notes, "CE4007 is relational: the note points at the extension"
    assert notes[0].span.line == 7, notes[0].span


def test_a_plain_target_still_reports_once(analyze):
    source = ("perk Sz:\n    fn sz() i32\n\nstruct B:\n    i32 v\n\n"
              "extend B sz() i32:\n    return 1\n\n"
              "extend B with Sz:\n    fn sz() i32:\n        return 2\n\n"
              "fn main() i32:\n    let B a = B(1)\n    println(\"{a.v}\")\n"
              "    return Result.Ok(0)\n")
    reporter = analyze(source, name="m")
    assert _codes(reporter, "CE4007") == ["CE4007"], [item.code for item in reporter.items]


def test_a_template_implementation_with_no_conflict_reports_nothing(analyze):
    source = (_HEAD + "extend Box@(T) other() i32:\n    return 1\n\n" + _IMPL + _TWO)
    reporter = analyze(source, name="m")
    assert not reporter.has_errors, [item.code for item in reporter.items]
