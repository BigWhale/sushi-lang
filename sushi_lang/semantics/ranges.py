"""The one reader of a RANGE whose bounds the compiler can read (#478).

A range appears in two positions that must agree: `foreach(i in 0..5)` steps a counter, and
`from([0..5])` fills five slots. The rules used to live in the back end's loop emitter alone,
so any other reader would have re-derived the direction and the inclusive adjustment.

The formula is the whole of it:

    count = |end - start| + (inclusive ? 1 : 0)
    step  = (end >= start) ? +1 : -1
    slot i holds first + step * i

It has no branch. `foreach` needs one because it compares a counter against the end; a fill
does not, because it knows the count before it starts.

The formula is stated twice, because it must be: here over Python integers, and in
`backend/ranges.py` as IR for the bounds the compiler cannot read.
`tests/unit/test_range_plan_matrix.py` pins the two against each other.

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

    span = abs(end - start)
    return RangePlan(
        first=start,
        step=1 if end >= start else -1,
        count=span + 1 if expr.inclusive else span,
        inclusive=expr.inclusive,
        loc=expr.loc,
    )


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
