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








def test_every_operand_kind_is_classified():
    """No kind may sit outside the table without a decision."""
    assert WRAPPERS <= set(OPERANDS)
    assert "bool" in OPERANDS, "the one accepted kind must be exercised too"






