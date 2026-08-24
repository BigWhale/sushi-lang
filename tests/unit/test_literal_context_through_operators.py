"""A declared type reaches a bare literal through every operator that KEEPS its type.

One recursion carries a declared numeric type down to the literal leaves, and the
operators it descends through are closed sets. `~` was in none of them: it is neither a
`BinaryOp` nor the negated bare literal the leaf stamper unwraps, so `let u8 b = ~0` left
its literal at the i32 default and the declaration was CE2002 (#448). Unary minus was only
half in -- `-(1 + 2)` failed the same way.

The table below asks the question per (operator, width) rather than per symptom, so a set
that loses a member again cannot pass. `not` is the control: it yields a bool, it is NOT
type-preserving, and a rule that descends through every unary operator fails that row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# One expression per operator, built only from bare literals -- the declared type is the
# only thing that can type them -- and valued so it is legal at every width below.
TYPE_PRESERVING = {
    "+": "1 + 2",
    "-": "8 - 2",
    "*": "4 * 2",
    "/": "8 / 2",
    "%": "9 % 4",
    "&": "0x0F & 0x33",
    "|": "0x01 | 0x02",
    "^": "0x0F ^ 0x33",
    "<<": "1 << 2",
    ">>": "8 >> 2",
    "neg": "-(1 - 1)",
    "~": "~0",
}

# Both signednesses, narrowest and widest of each.
WIDTHS = ["u8", "u64", "i8", "i64"]

# Every way the pass reports a literal that never took its context type.
UNTYPED = ("CE2002", "CE2070", "CE2073", "CE2510")


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _let(width: str, expr: str) -> str:
    return (
        "fn main() i32:\n"
        f"    let {width} value = {expr}\n"
        "    println(\"{value}\")\n"
        "    return Result.Ok(0)\n"
    )


@pytest.mark.parametrize("op", sorted(TYPE_PRESERVING))
@pytest.mark.parametrize("width", WIDTHS)
def test_a_declared_type_reaches_the_literals_under_one_operator(analyze, width, op):
    """Each operator hands the declared type on to the literals it is built from."""
    codes = _codes(analyze(_let(width, TYPE_PRESERVING[op])))
    reported = [c for c in codes if c in UNTYPED]
    assert not reported, (width, op, TYPE_PRESERVING[op], codes)


def test_logical_not_is_not_type_preserving(analyze):
    """The control. `not` answers bool, so a numeric type must NOT flow through it."""
    codes = _codes(analyze(_let("u8", "not 0")))
    assert "CE2002" in codes, codes


@pytest.mark.parametrize("expr,width", [
    ("~(1 << 3)", "u8"),      # through a unary INTO a binary
    ("~~0", "u8"),            # through two unaries
    ("-(1 + 2)", "i8"),       # the other half of the missing arm
    ("~(0x0F | 0x30)", "u8"),
])
def test_nesting_reaches_the_innermost_literal(analyze, expr, width):
    """The recursion does not stop at the first operator it descends through."""
    codes = _codes(analyze(_let(width, expr)))
    reported = [c for c in codes if c in UNTYPED]
    assert not reported, (width, expr, codes)


def test_a_sibling_types_a_literal_under_a_unary_operator(analyze):
    """A comparison hands down no numeric type, so the bit test needs the sibling's."""
    src = (
        "fn main() i32:\n"
        "    let u8 flags = 0xF0\n"
        "    if ((flags & ~0x0F) != 0):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2510" not in codes, codes


def test_a_negative_literal_beside_an_unsigned_sibling_is_out_of_range(analyze):
    """Reading bareness through `neg` moves this from CE2510 to the code that fits it.

    `mask + -1` on a u8 used to be a mixed u8/i32 pair, which described the compiler's
    own default rather than the program: -1 is simply not a u8, exactly as it is not in
    `let u8 x = -1`.
    """
    src = (
        "fn main() i32:\n"
        "    let u8 mask = 0x0F\n"
        "    let u8 lowered = mask + -1\n"
        "    println(\"{lowered}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2073" in codes, codes
    assert "CE2510" not in codes, codes


def test_the_literal_is_still_range_checked_under_a_unary_operator(analyze):
    """Reaching the operand is no licence to ignore the width: 300 is no u8."""
    assert "CE2073" in _codes(analyze(_let("u8", "~300"))), "300 fitted a u8 under a `~`"


def test_a_typed_value_under_a_unary_operator_still_needs_a_cast(analyze):
    """This types LITERALS. A complemented u32 is still a u32, and `as` is the way across."""
    src = (
        "fn main() i32:\n"
        "    let u32 wide = 0x0F\n"
        "    let u8 narrow = ~wide\n"
        "    println(\"{narrow}\")\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2002" in _codes(analyze(src)), "a typed operand was converted silently"


def test_the_type_preserving_set_lives_in_one_place():
    """One list of operators. A second copy is a second rule, and it will drift."""
    sites = [path.relative_to(PROJECT_ROOT)
             for path in sorted((PROJECT_ROOT / "sushi_lang").rglob("*.py"))
             if "_TYPE_PRESERVING_UNARY" in path.read_text(encoding="utf-8")]
    assert len(sites) == 1, sites
    assert sites[0].as_posix() == "sushi_lang/semantics/passes/types/propagation.py", sites
