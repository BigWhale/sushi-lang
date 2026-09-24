"""A bare literal operand is range-checked against its SIBLING's type, and only once.

The other operand of a binary operator is the literal's context. The literal was
range-checked against the i32 default before the sibling could type it, so a value past
i32 was CE2070 beside an i64 or a u32 operand. `EXPECT_ERROR_CODE` is a substring match
and cannot pin that one fault gives one diagnostic, so the code LIST is asserted here.
"""
from __future__ import annotations

import pytest


def _program(declaration: str, condition: str) -> str:
    return (
        "fn main() i32:\n"
        f"    {declaration}\n"
        f"    if ({condition}):\n"
        "        println(\"yes\")\n"
        "    return Result.Ok(0)\n"
    )


@pytest.mark.parametrize("declaration,condition", [
    ("let i64 big = 4294967301", "big > 4294967300"),
    ("let i64 big = 4294967301", "4294967300 < big"),
    ("let i64 big = 4294967301", "big + 4294967296 > 0"),
    ("let i64 big = 4294967301", "(big & 0x1_0000_0000) != 0"),
    ("let i64 big = 4294967301", "big == -4294967301"),
    ("let u32 x = 3000000000", "x == 3000000000"),
    ("let u64 y = 1", "y < 18446744073709551615"),
])
def test_a_wide_literal_fits_its_sibling(analyze, declaration, condition):
    """A value that fits the sibling's type is legal, whatever the i32 default says."""
    codes = [item.code for item in analyze(_program(declaration, condition)).items]
    assert codes == [], codes


@pytest.mark.parametrize("declaration,condition", [
    ("let u8 b = 1", "b == 300"),
    ("let i64 big = 4294967301", "big == 18446744073709551616"),
    ("let u32 x = 1", "4294967296 > x"),
])
def test_a_literal_too_wide_for_its_sibling_is_ce2073_alone(analyze, declaration,
                                                            condition):
    """The one diagnostic names the sibling's type; the i32 default is not asked."""
    codes = [item.code for item in analyze(_program(declaration, condition)).items]
    assert codes == ["CE2073"], codes


def test_a_bare_pair_keeps_the_i32_default(analyze):
    """The control: with no typed sibling, the i32 range check still fires."""
    codes = [item.code for item in analyze(_program("let i32 n = 0", "4294967296 > 1")).items]
    assert "CE2070" in codes, codes
