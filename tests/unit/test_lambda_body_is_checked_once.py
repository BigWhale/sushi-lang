"""A lambda body is walked ONE time, so every fault in it is reported one time (#629).

A lifted lambda IS a function: its body holds the same statement nodes the lambda had, and
the `lift` pass hands that body to the same validator every other function gets. So the
`typecheck` pass does not descend into it. What stays with `visit_lambda` is the type and
the capture rules -- CE2094 has no lifted function to be read off.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ast import Return
from sushi_lang.semantics.passes.types import TypeValidator


BLOCK_BODY = """fn main() i32:
    let fn(i32) -> i32 f = |i32 n|:
        return missing_fn(n)
    println(f(1).realise(0))
    return Result.Ok(0)
"""

EXPRESSION_BODY = """fn main() i32:
    let fn(i32) -> i32 f = |i32 n| missing_fn(n)
    println(f(1).realise(0))
    return Result.Ok(0)
"""

NESTED = """fn main() i32:
    let fn(i32) -> i32 outer = |i32 n|:
        let fn(i32) -> i32 inner = |i32 m| missing_fn(m)
        return inner(n)
    println(outer(1).realise(0))
    return Result.Ok(0)
"""

BORROW_CAPTURE = """fn main() i32:
    let i32 n = 1
    let peek i32 r = n
    let fn(i32) -> i32 f = |i32 m| m + r
    println(f(1).realise(0))
    return Result.Ok(0)
"""


def count(reporter, code: str) -> int:
    """How many diagnostics carry `code`."""
    return sum(1 for d in reporter.items if d.code == code)


@pytest.fixture
def return_lines(monkeypatch):
    """Record the source line of every `return` the typecheck walk validates."""
    seen: list[int] = []
    original = TypeValidator._validate_statement

    def counting(self, stmt):
        if isinstance(stmt, Return) and stmt.loc is not None:
            seen.append(stmt.loc.line)
        return original(self, stmt)

    monkeypatch.setattr(TypeValidator, "_validate_statement", counting)
    return seen


def test_a_block_body_statement_is_walked_once(analyze, return_lines):
    """The bug: the `return` on line 3 was validated by typecheck and again by lift."""
    analyze(BLOCK_BODY)
    assert return_lines.count(3) == 1, return_lines


def test_a_block_body_fault_is_reported_once(analyze):
    reporter = analyze(BLOCK_BODY)
    assert count(reporter, "CE2008") == 1


def test_a_block_body_return_fault_is_reported_once(analyze):
    """The second fault of the same statement doubled with the first."""
    reporter = analyze(BLOCK_BODY)
    assert count(reporter, "CE2030") == 1


def test_an_expression_body_fault_is_reported_once(analyze):
    """Not a block-body property: the synthesized `Result.Ok(...)` body doubled too."""
    reporter = analyze(EXPRESSION_BODY)
    assert count(reporter, "CE2008") == 1


def test_a_nested_lambda_fault_is_reported_once(analyze):
    """An inner lambda lifts out of the lifted outer body, so it must not multiply."""
    reporter = analyze(NESTED)
    assert count(reporter, "CE2008") == 1


def test_a_borrow_capture_is_still_refused_once(analyze):
    """CE2094 belongs to the lambda, not to the lifted function: it survives, once."""
    reporter = analyze(BORROW_CAPTURE)
    assert count(reporter, "CE2094") == 1
