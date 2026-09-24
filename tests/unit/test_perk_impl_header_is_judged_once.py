"""The header of a perk implementation on a generic target is judged once (#811).

Each instantiation of `Box@(T)` gets its own copy of `extend Box@(T) with P`, and the
copies are implementations too. The contract check read every copy, so one method the
source wrote once reported CE4004, CE4005 or CE0133 once for each instantiation. The
header is written once, in the template, and the template is where it is judged.

`EXPECT_ERROR_CODE` matches a SUBSTRING of the whole output, so no fixture can pin the
count. This module counts the codes.
"""
from __future__ import annotations

import pytest

_TYPES = "struct Box@(T):\n    T v\n\n"

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

_SIGNATURE = (
    "perk Sz:\n    fn sz() i32\n\n" + _TYPES
    + "extend Box@(T) with Sz:\n    fn sz() bool:\n        return true\n\n"
)

_MISSING = (
    "perk Two:\n    fn one() i32\n    fn two() i32\n\n" + _TYPES
    + "extend Box@(T) with Two:\n    fn one() i32:\n        return 1\n\n"
)

_CHANNEL = (
    "enum Oops:\n    Bad\n\n"
    "perk Ch:\n    fn get() i32 | Oops\n\n" + _TYPES
    + "extend Box@(T) with Ch:\n    fn get() i32:\n        return 1\n\n"
)


def _codes(reporter, code: str) -> list[str]:
    return [item.code for item in reporter.items if item.code == code]


@pytest.mark.parametrize("header,code", [
    (_SIGNATURE, "CE4004"),
    (_MISSING, "CE4005"),
    (_CHANNEL, "CE0133"),
], ids=["signature", "missing_method", "channel"])
@pytest.mark.parametrize("main", [_THREE, _ONE], ids=["three_instances", "one_instance"])
def test_a_header_fault_is_reported_once(analyze, header, code, main):
    reporter = analyze(header + main, name="m")
    assert _codes(reporter, code) == [code], [item.code for item in reporter.items]


def test_the_header_is_judged_where_it_is_written(analyze):
    """No instantiation, and the written declaration still carries the fault."""
    reporter = analyze(_SIGNATURE + _NONE, name="m")
    assert _codes(reporter, "CE4004") == ["CE4004"], [item.code for item in reporter.items]


def test_a_matching_header_reports_nothing(analyze):
    source = ("perk Sz:\n    fn sz() i32\n\n" + _TYPES
              + "extend Box@(T) with Sz:\n    fn sz() i32:\n        return 1\n\n" + _THREE)
    reporter = analyze(source, name="m")
    assert not reporter.has_errors, [item.code for item in reporter.items]
