"""The one reader of an array element that fills more than one slot (#446, #478).

Two elements do: `value; count` repeats one value, and `a..b` yields a sequence. Both are
ELEMENT forms -- they mix with plain elements in one literal, so a run's absolute position is
the sum of the counts before it. Three callers need that expansion -- the typecheck pass for
CE2011 and the element rules, the constant evaluator, and the back end -- and they must
agree, so they all read it here.

A range is a run whose value CHANGES with the slot. That is the smallest generalization, and
it is why CE2011's note ("run 2 fills 3..7 (5 elements)") stays correct for a range at no
cost.

**A count need not be readable.** Ruling 3: a run-time count is legal exactly where a
run-time LENGTH is, which is `from()` and nothing else. So this module records `count=None`
and reports nothing; a caller that needs a number calls `require_readable_length`, which
raises CE2017 for a repeated value and CE2019 for a range.

The count is read through a callback rather than by importing the evaluator. The typecheck
pass hands in a reader backed by the real reporter; the back end hands in a silent one, the
way `ASTBuilder.integer_constant` already does for a fixed array size. That keeps this
module free of an import cycle with `passes/const_eval.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import ArrayElement, Expr, IntLit, RangeExpr

if TYPE_CHECKING:
    from sushi_lang.semantics.ranges import RangePlan

# Reads an expression the compiler must know the value of, or None when it cannot.
ReadInt = Callable[[Expr], Optional[int]]


@dataclass(frozen=True)
class Run:
    """One element and how many slots it fills, with the first slot it fills."""
    value: Expr
    count: Optional[int]        # None: the count is only known at run time
    start: Optional[int]        # None: a run-time count sits before this run
    loc: Optional[Span]
    plan: Optional["RangePlan"] = None   # set when `value` is a readable RangeExpr
    count_expr: Optional[Expr] = None    # set when `count` is None: the count to emit
    is_repeat: bool = False              # written `value; count`, so the value BORROWS

    @property
    def is_range(self) -> bool:
        """True when this run yields a SEQUENCE rather than one value repeated."""
        return isinstance(self.value, RangeExpr)

    @property
    def end(self) -> int:
        """The LAST index this run fills. A run always fills at least one slot."""
        if self.start is None or self.count is None:
            raise ValueError("a run with a run-time count has no readable end")
        return self.start + self.count - 1


def read_runs(elements: Sequence[ArrayElement], read_int: ReadInt,
              reporter: Reporter) -> Optional[List[Run]]:
    """Every element as a run, or None when an element cannot fill slots at all.

    A plain element is a run of one, so a caller never asks whether an element repeats. A
    count the compiler cannot read is recorded as None and is NOT an error here -- see
    `require_readable_length`. A count it CAN read is checked at once, because Ruling 2 says
    a count you can see that spells nothing is a typo in every position.
    """
    from sushi_lang.semantics.ranges import read_range

    runs: List[Run] = []
    start: Optional[int] = 0
    failed = False

    for element in elements:
        if isinstance(element.value, RangeExpr):
            # Ruling 5: `value; count` repeats ONE value, and a range is not one value.
            if element.count is not None:
                er.emit(reporter, er.ERR.CE2020, element.count.loc)
                failed = True
                continue

            plan = read_range(element.value, read_int, reporter)
            if plan is not None and plan.count < 1:
                er.emit(reporter, er.ERR.CE2019, element.value.loc,
                        reason="this range yields no value, and Sushi has no "
                               "zero-length array")
                failed = True
                continue

            count = plan.count if plan is not None else None
            runs.append(Run(value=element.value, count=count, start=start,
                            loc=element.loc, plan=plan))
            start = None if count is None or start is None else start + count
            continue

        count = 1
        if element.count is not None:
            read = read_int(element.count)
            if read is not None and read < 1:
                er.emit(reporter, er.ERR.CE2017, element.count.loc,
                        reason=f"the count must be 1 or more, and this one is {read}")
                failed = True
                continue
            count = read

        runs.append(Run(value=element.value, count=count, start=start, loc=element.loc,
                        count_expr=element.count if count is None else None,
                        is_repeat=element.count is not None))
        start = None if count is None or start is None else start + count

    return None if failed else runs


def require_readable_length(runs: Sequence[Run], reporter: Reporter) -> Optional[int]:
    """The literal's length, or None after reporting the first run that has no readable one.

    Ruling 3: a fixed array's length is part of its TYPE and a constant's evaluator needs the
    values, so both need a number here. `from()` does not, and never calls this.
    """
    for run in runs:
        if run.count is not None:
            continue
        if run.is_range:
            er.emit(reporter, er.ERR.CE2019, run.value.loc,
                    reason="a bound must be a value the compiler can read here: a literal "
                           "in any base, or an integer constant. `from()` accepts any i32 "
                           "expression, because a `T[]` carries its length at run time")
        else:
            er.emit(reporter, er.ERR.CE2017, run.value.loc,
                    reason="the count must be a value the compiler can read here: a literal "
                           "in any base, or an integer constant. `from()` accepts any i32 "
                           "expression, because a `T[]` carries its length at run time")
        return None
    return expanded_length(runs)


def const_int_reader(const_table, ast_constants,
                    unit_name: Optional[str] = None) -> ReadInt:
    """A count reader backed by the constant evaluator. Always silent.

    CE2017 is the diagnostic a bad count gets, and `read_runs` is what raises it. A reader
    that spoke as well would put CE0108 beside CE2017 for one mistake.
    """
    def read(expr: Expr) -> Optional[int]:
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        from sushi_lang.semantics.typesys import BuiltinType

        evaluated = ConstantEvaluator(
            Reporter(), const_table, ast_constants, unit_name,
        ).evaluate(expr, BuiltinType.I32, expr.loc)
        if evaluated is None or isinstance(evaluated.value, bool):
            return None
        return evaluated.value if isinstance(evaluated.value, int) else None

    return read


def expanded_length(runs: Sequence[Run]) -> Optional[int]:
    """How many slots the literal fills, or None when one count is only known at run time.

    This is the count CE2011 compares, so a caller that needs it goes through
    `require_readable_length`, which reports before it answers None.
    """
    total = 0
    for run in runs:
        if run.count is None:
            return None
        total += run.count
    return total


def has_run(elements: Sequence[ArrayElement]) -> bool:
    """True when any element fills more than one slot -- a repeat or a range.

    A literal of plain elements keeps its old rendering.
    """
    return any(element.count is not None or isinstance(element.value, RangeExpr)
               for element in elements)


def values(elements: Sequence[ArrayElement]) -> List[Expr]:
    """The values that reach an ownership SINK: one value, one slot, one position.

    Two elements are excluded, and both for Ruling 7's reason -- a write that fills N slots
    has no single position to consume into.

    A RANGE owns nothing at all: it yields i32 by Ruling 4, and a caller walking values
    would type it as the `Iterator@(i32)` it is not. Its bounds are ordinary expressions --
    see `range_bounds`.

    A REPEATED value BORROWS. `[towel; 3]` copies three times and leaves `towel` usable,
    the same answer `.fill()` gives for the same reason (#479). See `repeated_values`.
    """
    return [element.value for element in elements
            if not isinstance(element.value, RangeExpr) and element.count is None]


def repeated_values(elements: Sequence[ArrayElement]) -> List[Expr]:
    """The values of `value; count` elements, which BORROW rather than consume (Ruling 7)."""
    return [element.value for element in elements
            if element.count is not None and not isinstance(element.value, RangeExpr)]


def range_bounds(elements: Sequence[ArrayElement]) -> List[Expr]:
    """The bound expressions of every range element, for a walk that must see them.

    The scope and borrow passes need these: `from([0..xs.len()])` uses `xs`, so a moved `xs`
    must still be reported.
    """
    out: List[Expr] = []
    for element in elements:
        if isinstance(element.value, RangeExpr):
            out.extend([element.value.start, element.value.end])
    return out


def expand(runs: Sequence[Run]) -> List[Expr]:
    """One expression per slot.

    For the constant evaluator, which holds a Python list either way, and which reaches this
    only after `require_readable_length` answered. A caller that emits code must NOT use
    this: the back end fills a run with a loop, never N stores.
    """
    out: List[Expr] = []
    for run in runs:
        if run.plan is not None:
            out.extend(IntLit(loc=run.value.loc, value=v) for v in run.plan.values())
            continue
        if run.count is None:
            raise ValueError("expand() needs a readable count")
        out.extend([run.value] * run.count)
    return out
