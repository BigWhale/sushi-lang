"""A diagnostic in a constant initializer carets the NODE that failed, never the declaration.

The evaluator hands every top-level handler the span it was given, and the caller of a
`const` gives it the declaration's span. So an operator that failed at the top of an
initializer -- `1 & true`, `A + B`, `-true`, `"a" as i32`, `Y[0]`, `seven()` -- was
reported with a caret bar under the whole line `const i32 X = ...`, while the same
expression one level down (`1 + (2 & true)`) got its own span, and the same expression
in a body got the expression's span (#682). Three arithmetic paths had the right span
because their handler recomputed `expr.loc or span` by hand; the rest did not.

An `EXPECT_ERROR_CODE` directive cannot pin a column, so this gate asks the analyzer for
the span, per operator group and per non-operator node, and it asks CE2509 for the same
help the body path gives.
"""
from __future__ import annotations

import pytest


NAMES = 'const string A = "Mostly"\nconst string B = " Harmless"\n'
SEVEN = "fn seven() i32:\n    return Result.Ok(7)\n\n"
SCALAR = "const i32 Y = 4\n"

# label -> (prelude, declared type, initializer expression, code)
CELLS = {
    "arith-plus-bool":     ("",      "i32",    "1 + true",      "CE0110"),
    "arith-minus-str":     ("",      "i32",    '1 - "a"',       "CE0110"),
    "arith-mul-bool":      ("",      "i32",    "2 * false",     "CE0110"),
    "div-zero":            ("",      "i32",    "1 / 0",         "CE0112"),
    "div-bool":            ("",      "i32",    "1 / true",      "CE0110"),
    "mod-zero":            ("",      "i32",    "7 % 0",         "CE0112"),
    "mod-bool":            ("",      "i32",    "1 % true",      "CE0110"),
    "bit-and-bool":        ("",      "i32",    "1 & true",      "CE0110"),
    "bit-or-float":        ("",      "i32",    "1 | 2.5",       "CE0110"),
    "bit-xor-str":         ("",      "i32",    '1 ^ "a"',       "CE0110"),
    "shl-bool":            ("",      "i32",    "1 << true",     "CE0110"),
    "shl-negative":        ("",      "i32",    "1 << -1",       "CE0110"),
    "shr-float":           ("",      "i32",    "1 >> 2.0",      "CE0110"),
    "shr-negative":        ("",      "i32",    "1 >> -1",       "CE0110"),
    "and-int":             ("",      "bool",   "true and 1",    "CE0110"),
    "or-str":              ("",      "bool",   'false or "a"',  "CE0110"),
    "xor-int":             ("",      "bool",   "true xor 0",    "CE0110"),
    "eq-int-str":          ("",      "bool",   '1 == "a"',      "CE0110"),
    "ne-bool-int":         ("",      "bool",   "true != 1",     "CE0110"),
    "lt-bool-bool":        ("",      "bool",   "true < false",  "CE0110"),
    "le-str-int":          ("",      "bool",   '"a" <= 1',      "CE0110"),
    "gt-int-str":          ("",      "bool",   '1 > "a"',       "CE0110"),
    "ge-bool-bool":        ("",      "bool",   "true >= true",  "CE0110"),
    "string-plus-lits":    ("",      "string", '"a" + "b"',     "CE2509"),
    "string-plus-names":   (NAMES,   "string", "A + B",         "CE2509"),
    "neg-bool":            ("",      "i32",    "-true",         "CE0110"),
    "bitnot-float":        ("",      "i32",    "~1.5",          "CE0110"),
    "not-int":             ("",      "bool",   "not 1",         "CE0110"),
    "cast-str":            ("",      "i32",    '"a" as i32',    "CE0111"),
    "index-scalar":        (SCALAR,  "i32",    "Y[0]",          "CE0110"),
    "call":                (SEVEN,   "i32",    "seven()",       "CE0108"),
}

MAIN = "\nfn main() i32:\n    return Result.Ok(0)\n"

# The AST builder stamps a postfix node at its SUFFIX -- `[0]`, `()` -- and a body
# diagnostic on such a node sits there too (a CE2002 on `y[0]` carets the `[0]`). So
# these two cells start after the expression's first column, and only the node test
# below applies to them.
POSTFIX = {"index-scalar", "call"}


def _program(prelude: str, ty: str, expr: str) -> tuple[str, int, int]:
    """The source, and the (line, col) where the initializer expression starts."""
    decl = f"const {ty} X = "
    src = prelude + decl + expr + "\n" + MAIN
    line = prelude.count("\n") + 1
    return src, line, len(decl) + 1


def _first(reporter, code: str):
    items = [item for item in reporter.items if item.code == code]
    assert items, f"no {code} in {[item.code for item in reporter.items]}"
    return items[0]


def _helps(item) -> list[str]:
    return [sub.message for sub in item.sub if sub.kind == "help"]


def _corners(span) -> tuple[int, int, int, int]:
    return (span.line, span.col, span.end_line, span.end_col)


@pytest.mark.parametrize("label", list(CELLS), ids=list(CELLS))
def test_the_caret_is_under_the_initializer_node(analyze_program, label):
    prelude, ty, expr, code = CELLS[label]
    src, line, col = _program(prelude, ty, expr)
    analysis = analyze_program(src)
    item = _first(analysis.reporter, code)
    assert item.span is not None, f"{label}: {code} has no location at all"

    initializer = analysis.program.constants[-1].value   # X is declared last
    assert _corners(item.span) == _corners(initializer.loc), (
        f"{label}: {code} is at {_corners(item.span)}, the initializer node `{expr}` "
        f"is at {_corners(initializer.loc)}. A caret under the whole declaration "
        f"separates nothing."
    )
    if label not in POSTFIX:
        assert (item.span.line, item.span.col) == (line, col), (
            f"{label}: the expression `{expr}` starts at {line}:{col}"
        )


def test_string_plus_in_a_constant_gives_the_help_a_body_gives(analyze):
    """CE2509 is one rule; the constant path and the body path render it alike."""
    src, _line, _col = _program(NAMES, "string", "A + B")
    in_constant = _first(analyze(src), "CE2509")

    body = (
        "fn main() i32:\n"
        '    let string a = "Mostly"\n'
        '    let string b = " Harmless"\n'
        "    let string x = a + b\n"
        "    return Result.Ok(0)\n"
    )
    in_body = _first(analyze(body), "CE2509")
    assert (in_body.span.line, in_body.span.col) == (4, 20)

    assert _helps(in_body), "the body path lost its help line"
    assert _helps(in_constant) == _helps(in_body), (
        f"a constant says {_helps(in_constant)}, a body says {_helps(in_body)}"
    )
