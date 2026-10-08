"""The gate on #478's Phase 1: the Python formula and the IR formula must not drift.

`semantics/ranges.py` states the range formula over Python integers, and `backend/ranges.py`
states it as IR. A range always goes up: the count is never below zero, and `.rev()` only
changes where the walk starts and which way it steps. Two statements of one rule is a decision, not an accident -- a readable
range must never pay for the run-time mechanism, because llvmlite does not fold. This file
pins the Python half against a table, and `tests/array/range_element/` pins the IR half
against the same bounds through `test_range_matches_foreach.sushi`.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.ast import IntLit, Name, RangeExpr
from sushi_lang.semantics.ranges import read_range

# (start, end, inclusive, reverse, the values the range yields). A range always goes up:
# a range whose end is below its start yields nothing, and `.rev()` gives the same values,
# last first.
ROWS = [
    (0, 5, False, False, [0, 1, 2, 3, 4]),
    (0, 5, True, False, [0, 1, 2, 3, 4, 5]),
    (5, 0, False, False, []),
    (5, 0, True, False, []),
    (3, 3, False, False, []),
    (3, 3, True, False, [3]),
    (-2, 2, False, False, [-2, -1, 0, 1]),
    (2, -2, True, False, []),
    (7, 8, False, False, [7]),
    (0, 5, False, True, [4, 3, 2, 1, 0]),
    (0, 5, True, True, [5, 4, 3, 2, 1, 0]),
    (3, 3, False, True, []),
    (3, 3, True, True, [3]),
    (-2, 2, False, True, [1, 0, -1, -2]),
    (5, 0, False, True, []),
    (5, 0, True, True, []),
]

IDS = [f"{'rev ' if r else ''}{a}..{'=' if i else ''}{b}" for a, b, i, r, _ in ROWS]


def _range(start: int, end: int, inclusive: bool, reverse: bool = False) -> RangeExpr:
    return RangeExpr(loc=None, start=IntLit(loc=None, value=start),
                     end=IntLit(loc=None, value=end), inclusive=inclusive, reverse=reverse)


def _read_literal(expr):
    return expr.value if isinstance(expr, IntLit) else None


@pytest.mark.parametrize("start,end,inclusive,reverse,expected", ROWS, ids=IDS)
def test_the_formula_yields_these_values(start, end, inclusive, reverse, expected):
    plan = read_range(_range(start, end, inclusive, reverse), _read_literal, Reporter())
    assert plan is not None
    assert plan.values() == expected
    assert plan.count == len(expected)
    if expected:
        assert plan.first == expected[0]
        assert plan.last() == expected[-1]


@pytest.mark.parametrize("start,end,inclusive,reverse,expected", ROWS, ids=IDS)
def test_step_walks_the_values(start, end, inclusive, reverse, expected):
    """`slot i holds first + step * i` -- the rule the IR twin emits, checked here."""
    plan = read_range(_range(start, end, inclusive, reverse), _read_literal, Reporter())
    assert plan is not None
    assert [plan.first + plan.step * i for i in range(plan.count)] == expected


def test_an_unreadable_bound_is_not_an_error():
    """`read_range` answers None and reports nothing. The CALLER decides (Ruling 3)."""
    reporter = Reporter()
    expr = RangeExpr(loc=None, start=IntLit(loc=None, value=0),
                     end=Name(loc=None, id="n"), inclusive=False)
    assert read_range(expr, _read_literal, reporter) is None
    assert not reporter.items


@pytest.mark.parametrize("start,end,inclusive,reverse,expected", ROWS, ids=IDS)
def test_a_range_goes_down_only_when_its_end_is_below_its_start(start, end, inclusive,
                                                                reverse, expected):
    """CE2125 reads this: the bounds go down, whatever the operator and `.rev()`."""
    plan = read_range(_range(start, end, inclusive, reverse), _read_literal, Reporter())
    assert plan is not None
    assert plan.goes_down == (start > end)


def test_the_step_of_a_single_inclusive_slot_does_not_matter():
    """`3..=3` yields one value, and it is `first + 0`, so the sign is irrelevant."""
    plan = read_range(_range(3, 3, True), _read_literal, Reporter())
    assert plan is not None
    assert plan.count == 1
    assert plan.values() == [3]
