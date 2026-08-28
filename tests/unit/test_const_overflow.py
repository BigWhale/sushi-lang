"""An operation the compiler reads computes at the declared width (Ruling 1, #446).

The evaluator held a Python integer of unlimited size and marked the exact result with
the type of the left operand, so a constant could hold a value its type cannot: `200 +
100` on a u8 held 300. Truncation hid it for `+`, `-`, `*`, `<<` and `~`, and exposed it
for `/`, `%`, `>>`, a comparison, a widening cast, an array index and an array size --
each of those reads the held value, so each disagreed with a body.

Two rules answer it, and the table below asks the question per operator so a set that
loses a member cannot pass:

- an OVERFLOW-CHECKED operator (`+ - * / %`, unary `-`) reports CE2077;
- a WIDTH-DEFINED operator (`~ & | ^ << >>`) computes at the width and never reports.

`docs/design/compile-time-evaluation.md` is normative.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.integer_width import fits_integer_type
from sushi_lang.semantics.typesys import BuiltinType


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _const(ty: str, expr: str) -> str:
    return (
        f"const {ty} VALUE = {expr}\n"
        "\n"
        "fn main() i32:\n"
        "    println(VALUE)\n"
        "    return Result.Ok(0)\n"
    )


def _let(ty: str, expr: str) -> str:
    return (
        "fn main() i32:\n"
        f"    let {ty} value = {expr}\n"
        "    println(value)\n"
        "    return Result.Ok(0)\n"
    )


# One expression per checked operator, valued so only the operation leaves the type.
CHECKED = {
    "+": ("u8", "200 + 100"),
    "-": ("u8", "100 - 200"),
    "*": ("u8", "200 * 2"),
    "/": ("i8", "SMALLEST / -1"),
    "%": ("u8", "(200 * 2) % 3"),
    "neg": ("i8", "-SMALLEST"),
}

# One expression per width-defined operator, with a result the width alone decides.
WIDTH_DEFINED = {
    "~": ("u32", "~(0 as u32)", 4294967295),
    "&": ("u8", "(0xFF as u8) & (0x0F as u8)", 15),
    "|": ("u8", "(0xF0 as u8) | (0x0F as u8)", 255),
    "^": ("u8", "(0xFF as u8) ^ (0x0F as u8)", 240),
    "<<": ("u8", "(200 as u8) << 1", 144),
    ">>": ("u32", "(~(0 as u32)) >> 1", 2147483647),
}


def _with_smallest(source: str) -> str:
    """The i8 rows need a name to negate and to divide: a literal leaf is not one."""
    return "const i8 SMALLEST = -128\n" + source


@pytest.mark.parametrize("op", sorted(CHECKED))
def test_a_checked_operator_reports_a_constant_that_leaves_its_type(analyze, op):
    """CE2077: the operation gives a value the declared type cannot hold."""
    ty, expr = CHECKED[op]
    codes = _codes(analyze(_with_smallest(_const(ty, expr))))
    assert "CE2077" in codes, (op, expr, codes)


@pytest.mark.parametrize("op", sorted(CHECKED))
def test_a_checked_operator_reports_the_same_fold_in_a_body(analyze, op):
    """One expression has one meaning, so a body reads it as a constant does."""
    ty, expr = CHECKED[op]
    codes = _codes(analyze(_with_smallest(_let(ty, expr))))
    assert "CE2077" in codes, (op, expr, codes)


@pytest.mark.parametrize("op", sorted(WIDTH_DEFINED))
def test_a_width_defined_operator_never_reports(analyze, op):
    """The bits that leave the width are lost, and that is the answer, not an error."""
    ty, expr, _ = WIDTH_DEFINED[op]
    codes = _codes(analyze(_const(ty, expr)))
    assert "CE2077" not in codes, (op, expr, codes)
    assert not codes, (op, expr, codes)


@pytest.mark.parametrize("op", sorted(WIDTH_DEFINED))
def test_a_width_defined_operator_holds_the_value_of_its_width(op):
    """The held value stops being a lie: it is the one the width leaves behind."""
    ty, expr, expected = WIDTH_DEFINED[op]
    assert _evaluate(_const(ty, expr)) == expected, (op, expr)


def test_an_unchecked_sum_of_two_locals_still_wraps(analyze):
    """Run time does not move. Only an expression the compiler reads is reported."""
    source = (
        "fn main() i32:\n"
        "    let u8 a = 200\n"
        "    let u8 b = 100\n"
        "    let u8 sum = a + b\n"
        "    println(sum)\n"
        "    return Result.Ok(0)\n"
    )
    assert not _codes(analyze(source))


def test_a_cast_truncates_and_does_not_report(analyze):
    """`as` is the escape: it asks for the bit pattern, so it never reports."""
    codes = _codes(analyze(_const("u8", "300 as u8")))
    assert not codes, codes
    assert _evaluate(_const("u8", "300 as u8")) == 44


def test_the_innermost_operation_reports_once(analyze):
    """The sum is the operation that leaves the type; the division around it is not."""
    codes = _codes(analyze(_const("u8", "(200 + 100) / 2")))
    assert codes.count("CE2077") == 1, codes


def test_a_constant_that_overflows_is_reported_where_it_is_declared(analyze):
    """A use of a broken constant adds no second report: the declaration owns it."""
    source = (
        "const u8 TOO_BIG = 200 + 100\n"
        "\n"
        "fn main() i32:\n"
        "    let u8 used = TOO_BIG + 1\n"
        "    println(used)\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(source))
    assert codes.count("CE2077") == 1, codes


def test_no_declared_type_reports_at_the_i32_default(analyze):
    """A bare literal defaults to i32, and the rule holds at that width."""
    source = (
        "fn main() i32:\n"
        "    println(2147483647 + 1)\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE2077" in _codes(analyze(source))


@pytest.mark.parametrize("ty,value,fits", [
    (BuiltinType.U8, 255, True),
    (BuiltinType.U8, 256, False),
    (BuiltinType.U8, -1, False),
    (BuiltinType.I8, 127, True),
    (BuiltinType.I8, 128, False),
    (BuiltinType.I8, -128, True),
    (BuiltinType.I8, -129, False),
    (BuiltinType.U64, 18446744073709551615, True),
    (BuiltinType.U64, 18446744073709551616, False),
    (BuiltinType.I64, -9223372036854775808, True),
    (BuiltinType.I64, 9223372036854775808, False),
    (BuiltinType.F64, 1, False),
])
def test_the_range_of_an_integer_type(ty, value, fits):
    """One table answers the range question, for the checker and for the literals."""
    assert fits_integer_type(value, ty) is fits


def _evaluate(source: str, name: str = "VALUE") -> object:
    """The value the evaluator holds for one constant, read straight out of it."""
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.semantics.passes.collect.constants import ConstantTable, ConstSig
    from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
    from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols

    program, _tree = parse_to_ast(source)
    by_name: UnitKeyedSymbols = UnitKeyedSymbols()
    table = ConstantTable()
    for const in program.constants:
        by_name.declare(const.name, const)
        table.declare(const.name, ConstSig(name=const.name, loc=const.loc,
                                           const_type=const.ty))

    wanted = by_name[name]
    held = ConstantEvaluator(Reporter(), table, by_name).evaluate(
        wanted.value, wanted.ty, wanted.loc)
    return held.value if held is not None else None
