"""Every condition position decides every operand kind, through ONE rule.

The gate for `reject_non_bool_condition`. An `if` and a `while` asked for a bool from
the start, but the logical operators asked nothing at all: `not 5` answered 0 with C
truthiness while `if (5)` was refused, and a string, a float, a struct, an enum or an
array operand reached the backend and became a CE0017 internal error (#532). A
`Result` or a `Maybe` was accepted everywhere and read for its Ok tag until #522.

The table is what keeps the nine positions in step: a rule that guards an `if` but
forgets the right operand of `xor` cannot pass it.
"""
from __future__ import annotations

import pytest


DECLARATIONS = (
    "struct Point:\n"
    "    i32 x\n"
    "\n"
    "enum Colour:\n"
    "    Red\n"
    "    Green\n"
    "\n"
    "fn blank() ~:\n"
    "    return Result.Ok(~)\n"
    "\n"
)

# Each entry declares one value named `a`.
OPERANDS = {
    "bool":   "    let bool a = true\n",
    "i32":    "    let i32 a = 1\n",
    "u8":     "    let u8 a = 1\n",
    "f64":    "    let f64 a = 1.0\n",
    "string": "    let string a = \"abc\"\n",
    "struct": "    let Point a = Point(1)\n",
    "enum":   "    let Colour a = Colour.Red()\n",
    "array":  "    let i32[] a = from([1, 2])\n",
    "result": "    let Result@(~, StdError) a = blank()\n",
    "maybe":  "    let Maybe@(i32) a = from([1, 2]).get(0)\n",
}

# A wrapper is told which predicate answers for it; everything else is CE2005.
WRAPPERS = {"result", "maybe"}

# Every place an expression is read as a condition. `{a}` is the operand under test.
POSITIONS = {
    "if":        "    if ({a}):\n        return Result.Ok(1)\n",
    "while":     "    while ({a}):\n        return Result.Ok(1)\n",
    "not":       "    if (not {a}):\n        return Result.Ok(1)\n",
    "and-left":  "    if ({a} and true):\n        return Result.Ok(1)\n",
    "and-right": "    if (true and {a}):\n        return Result.Ok(1)\n",
    "or-left":   "    if ({a} or false):\n        return Result.Ok(1)\n",
    "or-right":  "    if (false or {a}):\n        return Result.Ok(1)\n",
    "xor-left":  "    if ({a} xor true):\n        return Result.Ok(1)\n",
    "xor-right": "    if (true xor {a}):\n        return Result.Ok(1)\n",
}


def _program(kind: str, position: str) -> str:
    return (
        DECLARATIONS +
        "fn main() i32:\n" +
        OPERANDS[kind] +
        POSITIONS[position].format(a="a") +
        "    return Result.Ok(0)\n"
    )


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _helps(reporter, code: str) -> list[str]:
    """The help lines hanging off every diagnostic carrying this code."""
    return [sub.message
            for item in reporter.items if item.code == code
            for sub in item.sub if sub.kind == "help"]


def test_every_operand_kind_is_classified():
    """No kind may sit outside the table without a decision."""
    assert WRAPPERS <= set(OPERANDS)
    assert "bool" in OPERANDS, "the one accepted kind must be exercised too"


@pytest.mark.parametrize("position", sorted(POSITIONS))
@pytest.mark.parametrize("kind", sorted(OPERANDS))
def test_operand_in_every_condition_position(analyze, kind, position):
    """One rule answers all nine positions for every kind, and never with CE0017."""
    codes = _codes(analyze(_program(kind, position)))

    if kind == "bool":
        assert "CE2005" not in codes, (kind, position, codes)
        assert "CE2516" not in codes, (kind, position, codes)
    elif kind in WRAPPERS:
        assert "CE2516" in codes, (kind, position, codes)
    else:
        assert "CE2005" in codes, (kind, position, codes)

    # The point of the rule: nothing reaches the backend undecided.
    assert "CE0017" not in codes, (kind, position, codes)
    assert "CE0000" not in codes, (kind, position, codes)


@pytest.mark.parametrize("position", sorted(POSITIONS))
def test_an_integer_keeps_its_escape(analyze, position):
    """CE2005 offers `== 0` to an integer, which is the spelling that replaces it."""
    helps = _helps(analyze(_program("i32", position)), "CE2005")
    assert helps and all("== 0" in h for h in helps), (position, helps)


@pytest.mark.parametrize("kind", ["string", "struct", "array"])
def test_a_type_with_no_zero_is_offered_no_escape(analyze, kind):
    """`s != 0` does not spell anything, so the integer help must not be shown."""
    assert _helps(analyze(_program(kind, "if")), "CE2005") == [], kind
