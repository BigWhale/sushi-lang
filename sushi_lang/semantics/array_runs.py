"""The one reader of a repeated array element (#446, Ruling 2).

`value; count` repeats one element, and it is an ELEMENT form: runs and plain elements mix
in one literal, so a run's absolute position is the sum of the counts before it. Three
callers need that expansion -- the typecheck pass for CE2011 and the element rules, the
constant evaluator, and the back end -- and they must agree, so they all read it here.

The count is read through a callback rather than by importing the evaluator. The typecheck
pass hands in a reader backed by the real reporter; the back end hands in a silent one, the
way `ASTBuilder.integer_constant` already does for a fixed array size. That keeps this
module free of an import cycle with `passes/const_eval.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import ArrayElement, Expr

# Reads an expression the compiler must know the value of, or None when it cannot.
ReadInt = Callable[[Expr], Optional[int]]


@dataclass(frozen=True)
class Run:
    """One element and how many slots it fills, with the first slot it fills."""
    value: Expr
    count: int
    start: int
    loc: Optional[Span]

    @property
    def end(self) -> int:
        """The LAST index this run fills. A run always fills at least one slot."""
        return self.start + self.count - 1


def read_runs(elements: Sequence[ArrayElement], read_int: ReadInt,
              reporter: Reporter) -> Optional[List[Run]]:
    """Every element as a run, or None when a count is not a count.

    A plain element is a run of one, so a caller never asks whether an element repeats.
    """
    runs: List[Run] = []
    start = 0
    failed = False

    for element in elements:
        count = 1
        if element.count is not None:
            read = read_int(element.count)
            if read is None:
                er.emit(reporter, er.ERR.CE2017, element.count.loc,
                        reason="the count must be a value the compiler can read: a "
                               "literal in any base, or an integer constant")
                failed = True
                continue
            if read < 1:
                er.emit(reporter, er.ERR.CE2017, element.count.loc,
                        reason=f"the count must be 1 or more, and this one is {read}")
                failed = True
                continue
            count = read

        runs.append(Run(value=element.value, count=count, start=start, loc=element.loc))
        start += count

    return None if failed else runs


def const_int_reader(const_table, ast_constants) -> ReadInt:
    """A count reader backed by the constant evaluator. Always silent.

    CE2017 is the diagnostic a bad count gets, and `read_runs` is what raises it. A reader
    that spoke as well would put CE0108 beside CE2017 for one mistake.
    """
    def read(expr: Expr) -> Optional[int]:
        from sushi_lang.semantics.passes.const_eval import ConstantEvaluator
        from sushi_lang.semantics.typesys import BuiltinType

        evaluated = ConstantEvaluator(Reporter(), const_table, ast_constants).evaluate(
            expr, BuiltinType.I32, expr.loc)
        if evaluated is None or isinstance(evaluated.value, bool):
            return None
        return evaluated.value if isinstance(evaluated.value, int) else None

    return read


def expanded_length(runs: Sequence[Run]) -> int:
    """How many slots the literal fills. This is the count CE2011 compares."""
    return sum(run.count for run in runs)


def has_run(elements: Sequence[ArrayElement]) -> bool:
    """True when any element repeats. A literal of plain elements keeps its old rendering."""
    return any(element.count is not None for element in elements)


def values(elements: Sequence[ArrayElement]) -> List[Expr]:
    """The distinct value expressions, for a walk that does not care about counts."""
    return [element.value for element in elements]


def expand(runs: Sequence[Run]) -> List[Expr]:
    """One expression per slot.

    For the constant evaluator, which holds a Python list either way. A caller that emits
    code must NOT use this: the back end fills a run with a loop, never N stores.
    """
    out: List[Expr] = []
    for run in runs:
        out.extend([run.value] * run.count)
    return out
