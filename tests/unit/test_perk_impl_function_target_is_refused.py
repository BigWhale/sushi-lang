"""A function type is not a perk-implementation target, and the refusal is one diagnostic (#864).

The perk-implementation path follows the extension path's rule (#771): the collect pass
refuses a target that is a function type (CE2110) at the target, and records the refusal,
so a call of the perk method is not an undefined name (CE2008).

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
LIST is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]

_CASES = {
    "perks/function_target/test_err_perk_impl_function_target.sushi": ["CE2110"],
    "perks/function_target/test_err_perk_impl_function_target_two_calls.sushi": ["CE2110"],
}


def _codes(analyze, src: str) -> list[str]:
    return [item.code for item in analyze(src, name="pft").items
            if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_one_fault_is_one_diagnostic(analyze, fixture):
    assert _codes(analyze, (_TESTS / fixture).read_text()) == _CASES[fixture]


def test_control_a_struct_target_is_accepted(analyze):
    src = ("perk Seven:\n    fn seven() i32\n\nstruct Crate:\n    i32 w\n\n"
           "extend Crate with Seven:\n    fn seven() i32:\n        return 7\n\n"
           "fn main() i32:\n    let Crate c = Crate(1)\n    println(\"{c.seven()}\")\n"
           "    return Result.Ok(0)\n")
    assert _codes(analyze, src) == []
