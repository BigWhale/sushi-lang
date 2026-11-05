"""The one walk from a place expression to its root (#784).

A place is a chain of steps off a root: `a.b[i].c()??`. Each pass asks its own question
about the chain -- which local it reads out of, which constant it writes into, where a
call boundary cuts it -- but the WALK is one. A caller says which steps it crosses and
where it stops early; it never walks the chain itself.

The walk serves two trees: the AST, which the `scope` pass reads, and SHIR, which the
passes after it read (docs/design/ir.md 7.4). `Step` and `Walked` are the one vocabulary,
and the walk reads a node through the small adapter of its tree (`_ast_step`,
`_shir_step`), which answers the kind of the step and the node that it steps to.

The module sits outside both passes, so the typecheck pass does not import the borrow
pass. `tests/unit/test_place_walk_is_total.py` holds the walk total over the `Expr`
union and the `ShirExpr` union, and refuses a hand-rolled root walk anywhere else under
`semantics/`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sushi_lang.semantics.ast import (
    ArrayLiteral, BinaryOp, BlankLit, BoolLit, Borrow, Call, CastExpr, DotCall,
    DynamicArrayFrom, DynamicArrayNew, EnumConstructor, Expr, FloatLit, IndexAccess,
    InterpolatedString, IntLit, Lambda, MemberAccess, MethodCall, Name, RangeExpr, Spread,
    StringLit, TryExpr, TupleLiteral, UnaryOp,
)
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.shir.nodes import Hole, ShirNode


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
    DynamicArrayFrom, CastExpr, Borrow, RangeExpr, Spread, Lambda, TupleLiteral,
)

# The SHIR kinds that END a walk. A `Call` is not here: a dotted call is a step, and a call
# with no receiver ends the walk (`_shir_step`).
SHIR_NOT_A_STEP: tuple[type, ...] = (
    e.Lit, e.LocalRef, e.ConstRef, e.StorageRef, e.GlobalRef, e.Unary, e.Binary, e.Cast,
    e.Borrow, e.Take, e.Lambda, e.StructInit, e.EnumInit, e.TupleInit, e.ArrayInit,
    e.Range, e.Interp, Hole,
)


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

    @property
    def local(self) -> Optional[LocalId]:
        """The root as the local of a SHIR `LocalRef`, or None when the walk ended anywhere
        else."""
        return self.node.local if isinstance(self.node, e.LocalRef) else None


def walk_place(expr: Any, steps: Step, *,
               crosses_call: Optional[Callable[[Any], bool]] = None,
               stop: Optional[Callable[[Any], Any]] = None) -> Walked:
    """Walk `expr`, an AST or a SHIR expression, toward its root, crossing only the kinds
    in `steps`.

    `crosses_call` narrows `Step.CALL` to the calls it accepts. `stop` is asked at each
    member step before it is crossed; a non-None answer halts the walk there (an alias,
    a namespace), and the answer is kept in `Walked.stop`.
    """
    path: list[Any] = []
    node = expr
    while True:
        step = _shir_step(node) if isinstance(node, ShirNode) else _ast_step(node)
        # The backstop: a kind no arm crosses ends the walk, so a new expression kind is
        # never walked through by accident.
        if step is None or step[0] not in steps:
            return Walked(node, tuple(path))
        kind, inner = step
        if kind is Step.MEMBER and stop is not None:
            answer = stop(node)
            if answer is not None:
                return Walked(node, tuple(path), answer)
        if kind is Step.CALL and crosses_call is not None and not crosses_call(node):
            return Walked(node, tuple(path))
        path.append(node)
        node = inner


def is_get_call(node: Any) -> bool:
    """Whether the SHIR `node` is a `get()` call with no argument: in a place, the payload of
    an `Own@(T)`, which a reference may point into (#409). The caller asks the type."""
    return (isinstance(node, e.Call) and isinstance(node.target, e.Dotted)
            and node.target.name == "get" and not node.args)


def _ast_step(node: Optional[Expr]) -> Optional[tuple[Step, Expr]]:
    """The adapter of the AST: the kind of the step at `node` and the node it goes to."""
    match node:
        case MemberAccess():
            return Step.MEMBER, node.receiver
        case IndexAccess():
            return Step.INDEX, node.array
        case MethodCall() | DotCall():
            return Step.CALL, node.receiver
        case TryExpr():
            return Step.TRY, try_operand(node)
    return None


def _shir_step(node: ShirNode) -> Optional[tuple[Step, e.ShirExpr]]:
    """The adapter of SHIR: the kind of the step at `node` and the node it goes to. A
    slice is an element step, as an AST slice is an `IndexAccess`; a call steps to its
    receiver only when it has one."""
    match node:
        case e.Field():
            return Step.MEMBER, node.base
        case e.Index() | e.Slice():
            return Step.INDEX, node.base
        case e.Call(target=e.Dotted()):
            return Step.CALL, node.target.recv
        case e.Try():
            return Step.TRY, node.inner
    return None


def try_operand(expr: TryExpr) -> Expr:
    """What a `??` reads: its operand, with a read-through `or_err` call seen through.

    `X.or_err(nom e)??` is `X??` for every rule that reads the shape of a `??` operand:
    `or_err` changes nothing about what the place is or who owns it
    (docs/design/error-conversion.md section 4). So a get-out behind it is a get-out.
    """
    from sushi_lang.semantics.generics.builtin_signatures import read_through_receiver_of
    receiver = read_through_receiver_of(expr.expr)
    return receiver if receiver is not None else expr.expr


def unwrap_try(expr: Optional[Expr]) -> Optional[Expr]:
    """Strip every `??` from an expression, leaving what it actually evaluates.

    Each step reads through `try_operand`, so a read-through `or_err` is stripped too.
    """
    while isinstance(expr, TryExpr):
        expr = try_operand(expr)
    return expr
