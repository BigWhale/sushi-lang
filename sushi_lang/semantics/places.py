"""The one walk from a place expression to its root (#784).

A place is a chain of steps off a root: `a.b[i].c()??`. Each pass asks its own question
about the chain -- which local it reads out of, which constant it writes into, where a
call boundary cuts it -- but the WALK is one. A caller says which steps it crosses and
where it stops early; it never walks the chain itself.

The module sits outside both passes, so the typecheck pass does not import the borrow
pass. `tests/unit/test_place_walk_is_total.py` holds the walk total over the `Expr`
union and refuses a hand-rolled root walk anywhere else under `semantics/`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from sushi_lang.semantics.ast import (
    ArrayLiteral, BinaryOp, BlankLit, BoolLit, Borrow, Call, CastExpr, DotCall,
    DynamicArrayFrom, DynamicArrayNew, EnumConstructor, Expr, FloatLit, IndexAccess,
    InterpolatedString, IntLit, Lambda, MemberAccess, MethodCall, Name, RangeExpr, Spread,
    StringLit, TryExpr, UnaryOp,
)


class Step(enum.Flag):
    """The kinds of step a walk may cross on its way to the root."""

    NONE = 0
    MEMBER = enum.auto()   # `x.f`
    INDEX = enum.auto()    # `x[i]`
    CALL = enum.auto()     # `x.m(...)`, a MethodCall or a DotCall
    TRY = enum.auto()      # `x??`


# Every other expression kind ENDS a walk: it has no receiver a chain continues through.
NOT_A_STEP: tuple[type, ...] = (
    Name, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString,
    ArrayLiteral, UnaryOp, BinaryOp, Call, EnumConstructor, DynamicArrayNew,
    DynamicArrayFrom, CastExpr, Borrow, RangeExpr, Spread, Lambda,
)

MethodLike = Union[MethodCall, DotCall]


@dataclass(frozen=True)
class Walked:
    """Where a walk ended, and how it got there.

    `node` is the root, or the first node the walk did not cross (a call it may not cross,
    a literal, None). `path` holds every node the walk crossed, outermost first. `stop` is
    what the `stop` parameter answered when it halted the walk at `node`, else None.
    """

    node: Optional[Expr]
    path: tuple[Expr, ...] = ()
    stop: Any = None

    @property
    def name(self) -> Optional[Name]:
        """The root as a `Name`, or None when the walk ended anywhere else."""
        return self.node if isinstance(self.node, Name) else None


def walk_place(expr: Optional[Expr], steps: Step, *,
               crosses_call: Optional[Callable[[MethodLike], bool]] = None,
               stop: Optional[Callable[[MemberAccess], Any]] = None) -> Walked:
    """Walk `expr` toward its root, crossing only the kinds in `steps`.

    `crosses_call` narrows `Step.CALL` to the calls it accepts. `stop` is asked at each
    member step before it is crossed; a non-None answer halts the walk there (an alias,
    a namespace), and the answer is kept in `Walked.stop`.
    """
    path: list[Expr] = []
    node = expr
    while True:
        match node:
            case MemberAccess() if Step.MEMBER in steps:
                if stop is not None:
                    answer = stop(node)
                    if answer is not None:
                        return Walked(node, tuple(path), answer)
                path.append(node)
                node = node.receiver
            case IndexAccess() if Step.INDEX in steps:
                path.append(node)
                node = node.array
            case MethodCall() | DotCall() if (Step.CALL in steps and (
                    crosses_call is None or crosses_call(node))):
                path.append(node)
                node = node.receiver
            case TryExpr() if Step.TRY in steps:
                path.append(node)
                node = node.expr
            case _:
                # The backstop: a kind no arm crosses ends the walk, so a new expression
                # kind is never walked through by accident.
                return Walked(node, tuple(path))
