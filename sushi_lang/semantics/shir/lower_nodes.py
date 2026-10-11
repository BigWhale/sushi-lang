"""Body lowering: each AST statement and expression gives one SHIR node (docs/design/ir.md 7.10).

ONE table, `LOWERING`, gives the lowering of each AST body class that lowering handles,
keyed on the class (gate: `tests/unit/test_shir_lowering_is_one_table.py`). A class with
no row is a HOLE: lowering makes a `Hole` that names the AST class, records it, and the
compile goes on through the old pipeline. When the variable that `STRICT_VARIABLE` names
is `1`, a hole stops the compile with the stop code CE0155 instead. A value that carries
the `nom` marker is a hole too: it is a take (ir.md 7.4), and no row lowers a take yet.

A name lowers to the answer that the scope pass recorded for it (ir.md 7.1): a local, a
constant, a unit `var` or a function. A name with no declaration (`Unresolved`), and a
type or a namespace written as a value, is a hole: the program has an error, and SHIR has
no node for such a name.

Ruling E3 (ir.md 7.4): an operand whose value the typecheck pass reads is read here, with
the AST constant evaluator, and the value is recorded on its node (`readable`). These are
the divisor of `/` and `%`, the count of `<<` and `>>`, and each operand of the operators
that the overflow check folds (`+`, `-`, `*` and the negation). Lowering has no types, so
the evaluator reads at `i64`, the widest signed type; the typecheck pass applies the type
of the position. An operand is read only when it holds no local, no `var`, no function and
no hole: such an operand has no value that the compiler can read.

Every id comes from the counter of the body in the order of the walk, a node before its
operands, so a body gives the same ids on every run (ir.md 11.3).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeAlias, cast

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as a
from sushi_lang.semantics.shir import bindings as bd
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, ShirId
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.typesys import BuiltinType

if TYPE_CHECKING:
    from sushi_lang.semantics.const_eval import ConstantEvaluator

#: The variable that makes a hole stop the compile. One place reads it (gate:
#: `tests/unit/test_shir_strict_is_read_once.py`).
STRICT_VARIABLE = "SUSHI_SHIR_STRICT"

#: The name of this stage in the stop code.
STAGE = "lowering"

#: The operators whose operands the overflow check folds, and those whose right operand
#: alone the typecheck pass reads (a divisor, a shift count). Ruling E3.
_READ_BOTH = frozenset({"+", "-", "*"})
_READ_RIGHT = frozenset({"/", "%", "<<", ">>"})


def strict() -> bool:
    """True when a hole stops the compile (docs/compiler-reference.md, Dump SHIR)."""
    return os.environ.get(STRICT_VARIABLE) == "1"


@dataclass(slots=True)
class BodyLowerer:
    """The state of the lowering of one body.

    `holes` is the record of the program, shared by every body. `fallback` is the span of
    a node that has none of its own: the span of its declaration. `evaluator` reads the
    value of an operand (ruling E3); with no evaluator, no value is recorded. `opaque`
    counts the nodes made so far whose value the compiler cannot read.
    """
    ids: IdAllocator[ShirId]
    decls: DeclNames
    bindings: bd.Bindings
    holes: list[Hole]
    fallback: Span
    evaluator: Optional[ConstantEvaluator] = None
    opaque: int = field(default=0, init=False)

    def stmt(self, node: Any) -> s.ShirStmt:
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(s.ShirStmt, row(self, node))

    def expr(self, node: Any) -> e.ShirExpr:
        row = LOWERING.get(type(node))
        if row is None or node.nom_marked:
            return self.hole(node)
        return cast(e.ShirExpr, row(self, node))

    def operand(self, node: Any, read: bool) -> e.ShirExpr:
        """The operand `node`, with the value that the compiler reads when `read` is true."""
        before = self.opaque
        lowered = self.expr(node)
        if read and self.opaque == before and isinstance(lowered, e.ExprNode):
            lowered.readable = self.read(node)
        return lowered

    def read(self, node: Any) -> Optional[e.Constant]:
        if self.evaluator is None:
            return None
        from sushi_lang.semantics.const_eval import ScalarConstant

        value = self.evaluator.evaluate(node, BuiltinType.I64, None)
        if not isinstance(value, ScalarConstant):
            return None
        return _constant(value.value)

    def span(self, node: Any) -> Span:
        loc = getattr(node, "loc", None)
        return self.fallback if loc is None else loc

    def ident(self) -> ShirId:
        return self.ids.allocate()

    def made_opaque(self) -> ShirId:
        """The id of a node whose value the compiler cannot read."""
        self.opaque += 1
        return self.ident()

    def hole(self, node: Any) -> Hole:
        """A construct that lowering does not handle yet; CE0155 under the strict variable."""
        made = Hole(self.made_opaque(), self.span(node), type(node).__name__)
        if strict():
            er.raise_internal_error("CE0155", span=made.loc, stage=STAGE,
                                    construct=made.ast_class)
        self.holes.append(made)
        return made


def _constant(value: object) -> Optional[e.Constant]:
    """The value that the evaluator gives, as a SHIR constant with no type."""
    if isinstance(value, bool):
        return e.BoolConst(value)
    if isinstance(value, int):
        return e.IntConst(value)
    if isinstance(value, float):
        return e.FloatConst(value)
    if isinstance(value, str):
        return e.StrConst(value)
    return None


# -- literals ---------------------------------------------------------------------


def _int(lw: BodyLowerer, node: a.IntLit) -> e.Lit:
    return e.Lit(lw.ident(), lw.span(node), e.IntConst(node.value, node.radix, node.byte_spelling))


def _float(lw: BodyLowerer, node: a.FloatLit) -> e.Lit:
    return e.Lit(lw.ident(), lw.span(node), e.FloatConst(node.value))


def _bool(lw: BodyLowerer, node: a.BoolLit) -> e.Lit:
    return e.Lit(lw.ident(), lw.span(node), e.BoolConst(node.value))


def _string(lw: BodyLowerer, node: a.StringLit) -> e.Lit:
    return e.Lit(lw.ident(), lw.span(node), e.StrConst(node.value))


def _blank(lw: BodyLowerer, node: a.BlankLit) -> e.Lit:
    return e.Lit(lw.ident(), lw.span(node), e.BlankConst())


# -- names ------------------------------------------------------------------------


def _name(lw: BodyLowerer, node: a.Name) -> e.ShirExpr:
    """The answer of the scope pass. CE0153 when it recorded none: its backstop failed."""
    answer = lw.bindings.resolution_of(node)
    if answer is None:
        er.raise_internal_error("CE0153", span=node.loc, what="name", name=node.id)
    loc = lw.span(node)
    match answer:
        case bd.Local(local):
            return e.LocalRef(lw.made_opaque(), loc, local)
        case bd.Const(name):
            return e.ConstRef(lw.ident(), loc, lw.decls.refer(name))
        case bd.Storage(name):
            return e.StorageRef(lw.made_opaque(), loc, lw.decls.refer(name))
        case bd.Function(name):
            return e.GlobalRef(lw.made_opaque(), loc, lw.decls.refer(name))
    return lw.hole(node)


# -- operators --------------------------------------------------------------------


def _unary(lw: BodyLowerer, node: a.UnaryOp) -> e.Unary:
    ident = lw.ident()
    return e.Unary(ident, lw.span(node), node.op, lw.operand(node.expr, node.op == "neg"))


def _binary(lw: BodyLowerer, node: a.BinaryOp) -> e.Binary:
    ident = lw.ident()
    lhs = lw.operand(node.left, node.op in _READ_BOTH)
    rhs = lw.operand(node.right, node.op in _READ_BOTH or node.op in _READ_RIGHT)
    return e.Binary(ident, lw.span(node), node.op, lhs, rhs)


# -- statements -------------------------------------------------------------------


def _expr_stmt(lw: BodyLowerer, node: a.ExprStmt) -> s.ExprStmt:
    ident = lw.ident()
    return s.ExprStmt(ident, lw.span(node), lw.expr(node.expr))


def _return(lw: BodyLowerer, node: a.Return) -> s.Return:
    ident = lw.ident()
    return s.Return(ident, lw.span(node), lw.expr(node.value))


Lower: TypeAlias = Callable[[BodyLowerer, Any], "e.ShirExpr | s.ShirStmt"]

#: The lowering of each AST body class that lowering handles. A class with no row is a hole.
LOWERING: dict[type, Lower] = {
    a.IntLit: _int,
    a.FloatLit: _float,
    a.BoolLit: _bool,
    a.StringLit: _string,
    a.BlankLit: _blank,
    a.Name: _name,
    a.UnaryOp: _unary,
    a.BinaryOp: _binary,
    a.ExprStmt: _expr_stmt,
    a.Return: _return,
}
