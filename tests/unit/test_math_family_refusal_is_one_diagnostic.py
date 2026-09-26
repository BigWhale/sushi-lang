"""A math family call with refused arguments is one diagnostic per argument (#907).

The family answers the type of its row, and a first argument outside the family has no
row. The call then has no type, so the `let` that receives it adds no CE2002.
`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is absent, so the code
SET is asserted here, on the analyze path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[1]
_DIR = "stdlib/math/family_refused_args"

_CASES = {
    f"{_DIR}/test_err_math_family_refused_bare.sushi": {"CE2006"},
    f"{_DIR}/test_err_math_family_refused_alias.sushi": {"CE2006"},
    f"{_DIR}/test_err_math_family_refused_abs.sushi": {"CE2006"},
}


def _codes(analyze, src: str) -> set[str]:
    return {item.code for item in analyze(src, name="mf").items
            if item.code.startswith("CE")}


@pytest.mark.parametrize("fixture", list(_CASES))
def test_a_refused_family_call_is_one_fault(analyze, fixture):
    assert _codes(analyze, (_TESTS / fixture).read_text()) == _CASES[fixture]


def test_a_family_member_still_answers_its_type(analyze):
    """The control: a valid family call keeps its type, so a real mismatch is reported."""
    src = ("use <math>\n\nfn main() i32:\n    let string s = max(1.0, 2.0)\n"
           "    println(s)\n    return Result.Ok(0)\n")
    assert _codes(analyze, src) == {"CE2002"}


def test_the_family_answers_no_type_outside_its_rows():
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.sushi_stdlib.src.math import get_builtin_math_function_return_type

    returns = get_builtin_math_function_return_type
    assert returns("max", [BuiltinType.U8, BuiltinType.U8]) == BuiltinType.U8
    assert returns("abs", [BuiltinType.U8]) is None
    assert returns("max", [BuiltinType.STRING, BuiltinType.STRING]) is None
    assert returns("max", []) is None
