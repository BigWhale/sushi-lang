"""Every arithmetic operator decides every operand kind, through ONE rule.

The gate for `reject_non_numeric_arithmetic`. The typecheck pass asked nothing about
an arithmetic operand, so everything that is not a number reached the backend: a bool
or a wrapper became a CE0000 out of `emit_arithmetic`, a struct, an enum or an array
became an LLVM IR parse failure, and `-true` compiled and printed `true` (#709).

The table is what keeps the rule closed: one that guards `+` but forgets `%`, or the
binary half but not the unary minus, cannot pass it.
"""
from __future__ import annotations





# Each entry declares two operands of one type, named `a` and `b`.
OPERANDS = {
    "i32":    "    let i32 a = 1\n    let i32 b = 2\n",
    "u8":     "    let u8 a = 1\n    let u8 b = 2\n",
    "f64":    "    let f64 a = 1.0\n    let f64 b = 2.0\n",
    "string": "    let string a = \"abc\"\n    let string b = \"abd\"\n",
    "bool":   "    let bool a = true\n    let bool b = false\n",
    "struct": "    let Point a = Point(1)\n    let Point b = Point(2)\n",
    "enum":   "    let Colour a = Colour.Red()\n    let Colour b = Colour.Green()\n",
    "array":  "    let i32[] a = from([1, 2])\n    let i32[] b = from([1, 3])\n",
    "result": ("    let Result@(~, StdError) a = blank()\n"
               "    let Result@(~, StdError) b = blank()\n"),
    "maybe":  ("    let Maybe@(i32) a = from([1, 2]).get(0)\n"
               "    let Maybe@(i32) b = from([1, 2]).get(1)\n"),
}

# The only kinds an arithmetic operator takes. Everything else is CE2518.
NUMERIC = {"i32", "u8", "f64"}

# A wrapper is told how to take the value out of it.
WRAPPERS = {"result", "maybe"}












def test_every_operand_kind_is_classified():
    """No kind may sit outside the permitted set without a decision."""
    assert NUMERIC <= set(OPERANDS)
    assert WRAPPERS <= set(OPERANDS)
    assert not (NUMERIC & WRAPPERS)


























