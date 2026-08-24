"""The one reader of a repeated array element (#446, Ruling 2).

`value; count` is an ELEMENT form, so runs and plain elements mix in one literal and the
absolute position of each run falls out of the counts before it. Every caller -- the
typecheck pass for CE2011, the constant evaluator, and the back end -- reads the runs
through `read_runs` and nothing expands a literal twice.

The starts are what this gate is really about. CE2011 lists them, and a list that is wrong
sends the reader to the wrong run, which is worse than not listing them at all.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.array_runs import expanded_length, read_runs
from sushi_lang.semantics.ast import ArrayLiteral, IntLit, UnaryOp


def _literal(src: str) -> ArrayLiteral:
    """The literal of `const i32[N] A = <src>`, parsed and nothing more."""
    program, _tree = parse_to_ast(f"const i32[1] A = {src}\n")
    value = program.constants[0].value
    assert isinstance(value, ArrayLiteral)
    return value


def _read_int(expr):
    """A count reader for a bare literal, with unary minus. The real one evaluates."""
    if isinstance(expr, IntLit):
        return expr.value
    if isinstance(expr, UnaryOp) and expr.op == "-" and isinstance(expr.expr, IntLit):
        return -expr.expr.value
    return None


def _runs(src: str):
    reporter = Reporter()
    runs = read_runs(_literal(src).elements, _read_int, reporter)
    return runs, reporter


# (id, source, [(count, start), ...]). The last start plus its count is the total.
_SHAPES = [
    ("plain-only", "[1, 2, 3]", [(1, 0), (1, 1), (1, 2)]),
    ("one-run", "[0; 19]", [(19, 0)]),
    ("two-runs", "[0;2, 1;2]", [(2, 0), (2, 2)]),
    ("run-between-plain", "[1, 0;3, 9, 7]", [(1, 0), (3, 1), (1, 4), (1, 5)]),
    ("count-of-one", "[7;1, 8;1]", [(1, 0), (1, 1)]),
    ("zlib-fixed-lit", "[8;144, 9;112, 7;24, 8;8]",
     [(144, 0), (112, 144), (24, 256), (8, 280)]),
]


@pytest.mark.parametrize("_id,src,expected", _SHAPES, ids=[c[0] for c in _SHAPES])
def test_runs_carry_count_and_absolute_start(_id, src, expected):
    runs, reporter = _runs(src)
    assert not reporter.has_errors
    assert runs is not None
    assert [(r.count, r.start) for r in runs] == expected


@pytest.mark.parametrize("_id,src,expected", _SHAPES, ids=[c[0] for c in _SHAPES])
def test_expanded_length_is_the_sum_of_the_counts(_id, src, expected):
    runs, _reporter = _runs(src)
    assert expanded_length(runs) == sum(count for count, _start in expected)


def test_a_run_keeps_its_value_expression():
    """The value is read once. A run holds the expression, never a copy per element."""
    runs, _reporter = _runs("[8;144, 9;112]")
    assert [r.value.value for r in runs] == [8, 9]
    assert all(isinstance(r.value, IntLit) for r in runs)


# (id, source) -- every way a count is not a count. One code carries all of them.
_BAD_COUNTS = [
    ("zero", "[0; 0]"),
    ("negative", "[0; -2]"),
    ("unreadable", "[0; n]"),
]


@pytest.mark.parametrize("_id,src", _BAD_COUNTS, ids=[c[0] for c in _BAD_COUNTS])
def test_a_bad_count_is_ce2017_and_no_runs(_id, src):
    runs, reporter = _runs(src)
    assert runs is None
    assert [d.code for d in reporter.items] == ["CE2017"]


def test_a_silent_reporter_still_refuses_a_bad_count():
    """The back end reads with a silent reporter. It must still get None, not a guess."""
    runs, reporter = _runs("[0; 0]")
    assert runs is None
    assert reporter.has_errors
