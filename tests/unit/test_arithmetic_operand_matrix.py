"""Every arithmetic operator decides every operand kind, through ONE rule.

The gate for `reject_non_numeric_arithmetic`. The typecheck pass asked nothing about
an arithmetic operand, so everything that is not a number reached the backend: a bool
or a wrapper became a CE0000 out of `emit_arithmetic`, a struct, an enum or an array
became an LLVM IR parse failure, and `-true` compiled and printed `true` (#709).

The table is what keeps the rule closed: one that guards `+` but forgets `%`, or the
binary half but not the unary minus, cannot pass it.
"""
from __future__ import annotations

import pytest


BINARY_OPS = ["+", "-", "*", "/", "%"]

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


def _codes(reporter) -> list[str]:
    return [item.code for item in reporter.items]


def _helps(reporter, code: str) -> list[str]:
    """The help lines hanging off every diagnostic carrying this code."""
    return [sub.message
            for item in reporter.items if item.code == code
            for sub in item.sub if sub.kind == "help"]


def _binary(kind: str, op: str) -> str:
    return (
        DECLARATIONS +
        "fn main() i32:\n" +
        OPERANDS[kind] +
        f"    let i32 c = (a {op} b) as i32\n"
        "    println(\"{c}\")\n"
        "    return Result.Ok(0)\n"
    )


def _mixed(kind: str, op: str, kind_on_the_left: bool) -> str:
    """One numeric operand and one of `kind`, so each side is read on its own."""
    pair = f"a {op} 1" if kind_on_the_left else f"1 {op} a"
    return (
        DECLARATIONS +
        "fn main() i32:\n" +
        OPERANDS[kind] +
        f"    let i32 c = ({pair}) as i32\n"
        "    println(\"{c}\")\n"
        "    return Result.Ok(0)\n"
    )


def _negated(kind: str) -> str:
    return (
        DECLARATIONS +
        "fn main() i32:\n" +
        OPERANDS[kind] +
        "    let i32 c = (-a) as i32\n"
        "    println(\"{c}\")\n"
        "    return Result.Ok(0)\n"
    )


def test_every_operand_kind_is_classified():
    """No kind may sit outside the permitted set without a decision."""
    assert NUMERIC <= set(OPERANDS)
    assert WRAPPERS <= set(OPERANDS)
    assert not (NUMERIC & WRAPPERS)


@pytest.mark.parametrize("op", BINARY_OPS)
@pytest.mark.parametrize("kind", sorted(OPERANDS))
def test_operand_pair_of_one_type(analyze, kind, op):
    """One rule answers all five operators for every kind, and never with CE0000."""
    codes = _codes(analyze(_binary(kind, op)))

    if kind in NUMERIC:
        assert "CE2518" not in codes, (kind, op, codes)
    elif kind == "string" and op == "+":
        # The one carve-out: Sushi has no concatenation operator, and CE2509 names
        # the interpolation that replaces it. Two rules for one fault is one too many.
        assert "CE2509" in codes, (kind, op, codes)
        assert "CE2518" not in codes, (kind, op, codes)
    else:
        assert "CE2518" in codes, (kind, op, codes)

    # The point of the rule: nothing reaches the backend undecided.
    assert "CE0000" not in codes, (kind, op, codes)
    assert "CE0017" not in codes, (kind, op, codes)


@pytest.mark.parametrize("side", [True, False])
@pytest.mark.parametrize("op", BINARY_OPS)
@pytest.mark.parametrize("kind", sorted(set(OPERANDS) - NUMERIC))
def test_one_bad_operand_beside_a_number(analyze, kind, op, side):
    """Either side alone is enough; the rule reads both, not only the left."""
    codes = _codes(analyze(_mixed(kind, op, side)))

    if kind == "string" and op == "+":
        assert "CE2509" in codes, (kind, op, side, codes)
    else:
        assert "CE2518" in codes, (kind, op, side, codes)
    assert "CE0000" not in codes, (kind, op, side, codes)


@pytest.mark.parametrize("kind", sorted(OPERANDS))
def test_unary_minus_obeys_the_same_rule(analyze, kind):
    """Unary minus is arithmetic too: `-true` used to compile and print `true`."""
    codes = _codes(analyze(_negated(kind)))

    if kind in NUMERIC:
        assert "CE2518" not in codes, (kind, codes)
    else:
        assert "CE2518" in codes, (kind, codes)
    assert "CE0000" not in codes, (kind, codes)


@pytest.mark.parametrize("op", BINARY_OPS)
def test_a_mixed_numeric_pair_still_belongs_to_CE2510(analyze, op):
    """The width message keeps its own code, and the new rule stays out of the way."""
    src = (
        "fn main() i32:\n"
        "    let i32 a = 1\n"
        "    let i64 b = 2 as i64\n"
        f"    let i64 c = (a {op} b) as i64\n"
        "    println(\"{c}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2510" in codes, (op, codes)
    assert "CE2518" not in codes, (op, codes)


@pytest.mark.parametrize("kind", sorted(WRAPPERS))
def test_a_wrapper_is_told_how_to_take_its_value(analyze, kind):
    """A forgotten `??` is the common way to land here, so the escape is named."""
    helps = _helps(analyze(_binary(kind, "+")), "CE2518")
    assert helps and all("??" in h for h in helps), (kind, helps)


@pytest.mark.parametrize("kind", ["struct", "enum", "array", "bool"])
def test_a_plain_type_is_offered_no_wrapper_escape(analyze, kind):
    """`??` does not spell anything for a struct, so the wrapper help stays away."""
    helps = _helps(analyze(_binary(kind, "-")), "CE2518")
    assert helps == [], (kind, helps)


ZERO_DIVISORS = {
    "literal":  ("", "0"),
    "folded":   ("", "(3 - 3)"),
    "constant": ("const i32 ZERO = 0\n\n", "ZERO"),
    "negated":  ("", "(0 - 0)"),
}


@pytest.mark.parametrize("op", ["/", "%"])
@pytest.mark.parametrize("shape", sorted(ZERO_DIVISORS))
def test_a_readable_zero_divisor_is_refused(analyze, op, shape):
    """CE0112 in a body exactly as in a constant: one compile-time arithmetic."""
    preamble, divisor = ZERO_DIVISORS[shape]
    src = (
        preamble +
        "fn main() i32:\n"
        f"    let i32 x = 10 {op} {divisor}\n"
        "    println(\"{x}\")\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE0112" in _codes(analyze(src)), (op, shape)


@pytest.mark.parametrize("op", ["/", "%"])
def test_a_float_zero_divisor_is_refused(analyze, op):
    """The constant evaluator refuses it, so the body must agree."""
    src = (
        "fn main() i32:\n"
        f"    let f64 x = 10.0 {op} 0.0\n"
        "    println(\"{x}\")\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE0112" in _codes(analyze(src)), op


@pytest.mark.parametrize("op", ["/", "%"])
def test_a_divisor_the_compiler_cannot_read_is_left_alone(analyze, op):
    """A computed divisor is ordinary code; only a readable zero is a diagnostic."""
    src = (
        "fn main() i32:\n"
        "    let i32 d = 0\n"
        f"    let i32 x = 10 {op} d\n"
        "    println(\"{x}\")\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE0112" not in _codes(analyze(src)), op


@pytest.mark.parametrize("op", ["/", "%"])
def test_a_non_zero_divisor_is_left_alone(analyze, op):
    src = (
        "fn main() i32:\n"
        f"    let i32 x = 10 {op} 3\n"
        "    println(\"{x}\")\n"
        "    return Result.Ok(0)\n"
    )
    assert "CE0112" not in _codes(analyze(src)), op


@pytest.mark.parametrize("op", ["/", "%"])
def test_a_bad_divisor_reports_once(analyze, op):
    """`1 / false` is one fault: the operand rule, and not a zero divisor as well."""
    src = (
        "fn main() i32:\n"
        f"    let i32 x = 10 {op} false\n"
        "    println(\"{x}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE2518" in codes, (op, codes)
    assert "CE0112" not in codes, (op, codes)


def test_a_constant_keeps_its_own_diagnostic(analyze):
    """The evaluator owns a const initializer; the body rule must not speak twice."""
    src = (
        "const i32 BAD = 1 + true\n"
        "\n"
        "fn main() i32:\n"
        "    println(\"{BAD}\")\n"
        "    return Result.Ok(0)\n"
    )
    codes = _codes(analyze(src))
    assert "CE0110" in codes, codes
    assert codes.count("CE2518") <= 1, codes
