"""CE0107 on a lambda carets the lambda and names no internal symbol (#845, #846).

A `test_err_` directive sees the code and the text, not the span, so the location is
asserted here. A lifted lambda used to reach CE0107 with no span and the name
`__lambda_0`.
"""
from __future__ import annotations

import pytest

VALUE_LAMBDA = """\
fn main() i32:
    let fn(i32) -> i32 h = |i32 x|:
        println("h {x}")
    println("{h(1).realise(0)}")
    return Result.Ok(0)
"""

BLANK_LAMBDA = """\
fn main() i32:
    let fn() -> ~ g = |~|:
        println("g")
    match g():
        Result.Ok(_) -> println("ok")
        Result.Err(_) -> println("err")
    return Result.Ok(0)
"""


@pytest.mark.parametrize("src, col", [(VALUE_LAMBDA, 28), (BLANK_LAMBDA, 23)],
                         ids=["value", "blank"])
def test_lambda_fall_off_carets_the_lambda(analyze, src, col):
    reporter = analyze(src)
    found = [d for d in reporter.items if d.code == "CE0107"]
    assert len(found) == 1, [(d.code, d.message) for d in reporter.items]
    diag = found[0]
    assert diag.span is not None
    assert (diag.span.line, diag.span.col) == (2, col)
    assert "__lambda" not in diag.message
    assert "lambda" in diag.message
