"""The one reader of a RANGE whose bounds the compiler can read (#478).

A range appears in two positions that must agree: `foreach(i in 0..5)` steps a counter, and
`from([0..5])` fills five slots. Both read the formula here, so no reader derives the count
or the direction for itself.

A range ALWAYS goes up. `(a..b).rev()` yields the same values, last first. The formula is
the whole of it:

    count = max(end - start + (inclusive ? 1 : 0), 0)
    step  = reverse ? -1 : +1
    first = reverse ? last value (end - 1, or end when inclusive) : start
    slot i holds first + step * i

A range whose end is below its start is EMPTY. With readable bounds that is a typo, and the
caller reports CE2125 (`reject_a_range_that_goes_down`). With a computed bound it is data,
and nothing is reported. The old rule took the direction from the values, so the countdown
`(n - 1)..=0` walked -1 and 0 when n was 0. The countdown is `(0..n).rev()` now, and it is
empty when n is 0.

The formula is stated twice, because it must be: here over Python integers, and in
`backend/ranges.py` as IR for the bounds the compiler cannot read.
`tests/unit/test_range_plan_matrix.py` pins the Python half against a table.

The bounds are read through a callback rather than by importing the evaluator, the way
`array_runs.py` reads a repeat count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import RangeExpr
from sushi_lang.semantics.array_runs import ReadInt


@dataclass(frozen=True)
class RangePlan:
    """A range whose bounds the compiler could read, as values it will yield."""
    first: int
    step: int
    count: int
    inclusive: bool
    loc: Optional[Span]
    start: int
    end: int
    reverse: bool

    @property
    def goes_down(self) -> bool:
        """True when the written bounds go down. CE2125 refuses such a range."""
        return self.start > self.end

    def last(self) -> int:
        """The LAST value this range yields. Undefined for an empty range."""
        return self.first + self.step * (self.count - 1)

    def values(self) -> List[int]:
        """Every value, in order. For the constant evaluator and the short fill tier."""
        return [self.first + self.step * i for i in range(self.count)]


def read_range(expr: RangeExpr, read_int: ReadInt,
               reporter: Reporter) -> Optional[RangePlan]:
    """The plan for a range, or None when a bound is not readable.

    None is NOT an error by itself, and this function reports nothing. A `from()` array
    carries its length at run time and needs no plan; a fixed array and a constant do, and
    each raises its own diagnostic. Silent for the same reason `array_runs.const_int_reader`
    is: one mistake, one code.
    """
    start = read_int(expr.start)
    if start is None:
        return None
    end = read_int(expr.end)
    if end is None:
        return None

    count = max(end - start + (1 if expr.inclusive else 0), 0)
    return RangePlan(
        first=start + count - 1 if expr.reverse else start,
        step=-1 if expr.reverse else 1,
        count=count,
        inclusive=expr.inclusive,
        loc=expr.loc,
        start=start,
        end=end,
        reverse=expr.reverse,
    )


def reject_a_range_that_goes_down(plan: Optional[RangePlan], reporter: Reporter) -> bool:
    """CE2125 when both bounds are readable and the range goes down. True when reported.

    The `foreach` check and the array-element reader both call this, so the two positions
    give one diagnostic with one help. The help spells the countdown that yields the values
    the written bounds name: `10..0` is `(1..=10).rev()`, `10..=0` is `(0..=10).rev()`.
    """
    from sushi_lang.internals import errors as er

    if plan is None or not plan.goes_down:
        return False
    operator = "..=" if plan.inclusive else ".."
    written = f"{plan.start}{operator}{plan.end}"
    if plan.reverse:
        written = f"({written}).rev()"
    low = plan.end if plan.inclusive else plan.end + 1
    er.emit_with(reporter, er.ERR.CE2125, plan.loc, range=written) \
        .help(f"to count down from {plan.start} to {low}, write `({low}..={plan.start}).rev()`") \
        .emit()
    return True


def holds_a_range_value(root: object) -> bool:
    """True when a range under `root` is in neither of its two positions (CE2122, #1165).

    The typecheck pass's rule, read off the shape: a range is a `foreach` iterable or an
    array-literal element, and anything else is refused. A pass that types an expression
    before the typecheck pass reports the range reads this, so it solves nothing from a
    range -- a constraint judged against `Iterator@(i32)` was CE4006 in place of the
    range's own error.
    """
    from sushi_lang.semantics.ast import ArrayElement, Foreach
    from sushi_lang.semantics.ast_walk import children, walk_nodes

    found = False

    def visit(node) -> bool:
        nonlocal found
        if isinstance(node, RangeExpr):
            found = True
        if found:
            return False
        legal = (node.value if isinstance(node, ArrayElement)
                 else node.iterable if isinstance(node, Foreach) else None)
        if not isinstance(legal, RangeExpr):
            return True
        walk_nodes([legal.start, legal.end], visit)
        walk_nodes([child for child in children(node) if child is not legal], visit)
        return False

    walk_nodes(root, visit)
    return found
