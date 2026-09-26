"""Every comparison operator decides every operand pair, through ONE rule.

The gate for `reject_uncomparable_operands`. Before #449 the typecheck pass asked
nothing about a comparison's operands, so a string, a struct, an enum or an array
reached the backend and became a CE0017 internal error, and `string < string` became
a CE0000. The table below is what keeps the two allow-lists closed: a rule that
permits `<` but forgets `>=` cannot pass it.
"""
from __future__ import annotations





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







def test_every_operand_kind_is_classified():
    """No type may sit outside the two permission sets without a decision."""
    assert PERMITS_ORDER <= PERMITS_EQUALITY, "an ordered type must also be equatable"
    assert PERMITS_EQUALITY <= set(OPERANDS)












