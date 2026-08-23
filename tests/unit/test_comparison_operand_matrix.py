"""Every comparison operator decides every operand pair, through ONE rule.

The gate for `reject_uncomparable_operands`. Before #449 the typecheck pass asked
nothing about a comparison's operands, so a string, a struct, an enum or an array
reached the backend and became a CE0017 internal error, and `string < string` became
a CE0000. The table below is what keeps the two allow-lists closed: a rule that
permits `<` but forgets `>=` cannot pass it.
"""
from __future__ import annotations

import pytest


EQUALITY_OPS = ["==", "!="]
ORDER_OPS = ["<", ">", "<=", ">="]
ALL_OPS = EQUALITY_OPS + ORDER_OPS


# Each entry declares two operands of one type, named `a` and `b`.
OPERANDS = {
    "i32":    "    let i32 a = 1\n    let i32 b = 2\n",
    "u8":     "    let u8 a = 1\n    let u8 b = 2\n",
    "f64":    "    let f64 a = 1.0\n    let f64 b = 2.0\n",
    "string": "    let string a = \"abc\"\n    let string b = \"abd\"\n",
    "bool":   "    let bool a = true\n    let bool b = false\n",
    "struct": "    let Point a = Point(1, 2)\n    let Point b = Point(1, 3)\n",
    "enum":   "    let Colour a = Colour.Red()\n    let Colour b = Colour.Green()\n",
    "array":  "    let i32[] a = from([1, 2])\n    let i32[] b = from([1, 3])\n",
}

# What each type permits. A type absent from a group takes CE2514 for that group.
PERMITS_EQUALITY = {"i32", "u8", "f64", "string", "bool"}
PERMITS_ORDER = {"i32", "u8", "f64", "string"}

DECLARATIONS = (
    "struct Point:\n"
    "    i32 x\n"
    "    i32 y\n"
    "\n"
    "enum Colour:\n"
    "    Red\n"
    "    Green\n"
    "\n"
)


def _program(kind: str, op: str) -> str:
    return (
        DECLARATIONS +
        "fn main() i32:\n" +
        OPERANDS[kind] +
        f"    if (a {op} b):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
    )


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def test_every_operand_kind_is_classified():
    """No type may sit outside the two permission sets without a decision."""
    assert PERMITS_ORDER <= PERMITS_EQUALITY, "an ordered type must also be equatable"
    assert PERMITS_EQUALITY <= set(OPERANDS)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("kind", sorted(OPERANDS))
def test_operand_pair_of_one_type(analyze, kind, op):
    """One rule answers all six operators for every type, and never with CE0017."""
    permitted = PERMITS_EQUALITY if op in EQUALITY_OPS else PERMITS_ORDER
    codes = _codes(analyze(_program(kind, op)))

    if kind in permitted:
        assert "CE2513" not in codes, (kind, op, codes)
        assert "CE2514" not in codes, (kind, op, codes)
    else:
        assert "CE2514" in codes, (kind, op, codes)

    # The point of the rule: nothing reaches the backend undecided.
    assert "CE0017" not in codes, (kind, op, codes)
    assert "CE0000" not in codes, (kind, op, codes)


@pytest.mark.parametrize("op", ALL_OPS)
def test_string_against_an_integer_is_a_mixed_pair(analyze, op):
    """CE2513, for every operator -- not only the four that used to crash."""
    src = (
        "fn main() i32:\n"
        "    let string a = \"abc\"\n"
        "    let i32 b = 3\n"
        f"    if (a {op} b):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2513" in _codes(analyze(src)), op


@pytest.mark.parametrize("op", ALL_OPS)
def test_a_mixed_numeric_pair_still_belongs_to_CE2510(analyze, op):
    """The width message keeps its own code, and the new rule stays out of the way."""
    src = (
        "fn main() i32:\n"
        "    let i32 a = 1\n"
        "    let i64 b = 2 as i64\n"
        f"    if (a {op} b):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2510" in codes, (op, codes)
    assert "CE2513" not in codes, (op, codes)
    assert "CE2514" not in codes, (op, codes)


@pytest.mark.parametrize("op", ORDER_OPS)
def test_string_order_is_accepted(analyze, op):
    """The feature #449 asked for: all four order operators, not only `<`."""
    codes = _codes(analyze(_program("string", op)))
    assert "CE2513" not in codes and "CE2514" not in codes, (op, codes)


@pytest.mark.parametrize("op", ORDER_OPS)
def test_bool_has_no_order(analyze, op):
    """A deliberate divergence from Rust and Go, which both order false < true."""
    assert "CE2514" in _codes(analyze(_program("bool", op))), op


@pytest.mark.parametrize("op", EQUALITY_OPS)
def test_bool_keeps_its_equality(analyze, op):
    codes = _codes(analyze(_program("bool", op)))
    assert "CE2514" not in codes, (op, codes)
