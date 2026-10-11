"""Body lowering: each AST statement and expression gives one SHIR node (docs/design/ir.md 7.10).

ONE table, `LOWERING`, gives the lowering of each AST body class that lowering handles,
keyed on the class (gate: `tests/unit/test_shir_lowering_is_one_table.py`). A class with
no row is a HOLE: lowering makes a `Hole` that names the AST class, records it, and the
compile goes on through the old pipeline. When the variable that `STRICT_VARIABLE` names
is `1`, a hole stops the compile with the stop code CE0155 instead. A value that carries
the `nom` marker is a hole too: it is a take (ir.md 7.4), and no row lowers a take yet.

A call argument has a second table, `ARGUMENTS`: the classes that are the FORM of an
argument there, and no value of their own. `nom x`, `peek x` and `poke x` are the written
marker of the `Arg`, and `xs...` is a bloom.

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
no call and no hole: such an operand has no value that the compiler can read.

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
from sushi_lang.semantics.param_modes import Convention, receiver_mode
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import bindings as bd
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, ShirId
from sushi_lang.semantics.shir.lookup import DeclLookup
from sushi_lang.semantics.shir.nodes import Hole
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

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

#: The kinds of declaration behind a namespace that a call calls (`namespaces.Binding`).
_CALLABLE_KINDS = frozenset({"function", "generic function", "extern"})

#: The kinds of declaration behind a namespace that name a type.
_TYPE_KINDS = frozenset({"struct", "enum", "type"})


def strict() -> bool:
    """True when a hole stops the compile (docs/compiler-reference.md, Dump SHIR)."""
    return os.environ.get(STRICT_VARIABLE) == "1"


@dataclass(slots=True)
class BodyLowerer:
    """The state of the lowering of one body.

    `holes` is the record of the program, shared by every body. `fallback` is the span of
    a node that has none of its own: the span of its declaration. `evaluator` reads the
    value of an operand (ruling E3); with no evaluator, no value is recorded. `lookup`
    reads the declaration tables; with no lookup, a call on a type name or a namespace is
    a hole. `opaque` counts the nodes made so far whose value the compiler cannot read.
    """
    ids: IdAllocator[ShirId]
    decls: DeclNames
    bindings: bd.Bindings
    holes: list[Hole]
    fallback: Span
    evaluator: Optional[ConstantEvaluator] = None
    lookup: Optional[DeclLookup] = None
    opaque: int = field(default=0, init=False)

    def stmt(self, node: Any) -> s.ShirStmt:
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(s.ShirStmt, row(self, node))

    def expr(self, node: Any) -> e.ShirExpr:
        """The expression `node` in the position of a value, where `nom` is a take."""
        return self.hole(node) if node.nom_marked else self.value(node)

    def value(self, node: Any) -> e.ShirExpr:
        """The expression `node`; the caller reads the marker that it carries."""
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(e.ShirExpr, row(self, node))

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


# -- calls ------------------------------------------------------------------------


def _call(lw: BodyLowerer, node: a.Call) -> e.ShirExpr:
    """`f(...)`: the callee is `Named`, a name or any expression that gives a function.

    A struct name as the callee is a construction, and a named argument names a field of
    one; SHIR has no field for written type arguments (`identity@(i32)(5)`). Each is a
    hole until its lowering exists.
    """
    if (node.field_names is not None or node.type_args
            or isinstance(lw.bindings.resolution_of(node.callee), bd.TypeName)):
        return lw.hole(node)
    ident = lw.made_opaque()
    return e.Call(ident, lw.span(node), e.Named(lw.expr(node.callee)),
                  _arguments(lw, node.args))


def _dot_call(lw: BodyLowerer, node: a.DotCall) -> e.ShirExpr:
    return lw.hole(node) if node.type_args else _dotted(lw, node)


def _method_call(lw: BodyLowerer, node: a.MethodCall) -> e.ShirExpr:
    return _dotted(lw, node)


def _dotted(lw: BodyLowerer, node: a.DotCall | a.MethodCall) -> e.ShirExpr:
    """`x.f(...)`: what the scope pass resolved the name before the dot to decides it.

    A namespace gives the function that it holds, and a type name gives a static, also
    behind an alias (`hm.HashMap.new()`). Any other receiver is a value, and the target
    is `Dotted`: which method it calls is an answer of the typecheck pass (ir.md 7.4). A
    named argument is a hole, as in `_call`.
    """
    if node.field_names is not None:
        return lw.hole(node)
    match lw.bindings.resolution_of(node.receiver):
        case bd.Namespace(alias):
            return _qualified_call(lw, node, alias)
        case bd.TypeName(name):
            return _static_call(lw, node, _written_type(name))
    qualified = _qualified_type(lw, node.receiver)
    if qualified is not None:
        return _static_call(lw, node, qualified)
    ident = lw.made_opaque()
    return e.Call(ident, lw.span(node), e.Dotted(lw.expr(node.receiver), node.method),
                  _arguments(lw, node.args))


def _qualified_call(lw: BodyLowerer, node: a.DotCall | a.MethodCall,
                    alias: str) -> e.ShirExpr:
    """`ns.f(...)`: the alias folds into the written name of the function that it holds.

    Anything else that the namespace holds under the name, a struct included, is a hole.
    """
    kind = None if lw.lookup is None else lw.lookup.member_kind(lw.bindings.body, alias,
                                                                node.method)
    if kind not in _CALLABLE_KINDS:
        return lw.hole(node)
    ident = lw.made_opaque()
    ref = e.GlobalRef(lw.made_opaque(), lw.span(node), lw.decls.refer(f"{alias}.{node.method}"))
    return e.Call(ident, lw.span(node), e.Named(ref), _arguments(lw, node.args))


def _static_call(lw: BodyLowerer, node: a.DotCall | a.MethodCall,
                 ty: BuiltinType | UnknownType) -> e.ShirExpr:
    """`T.m(...)`: a static method of `T`, or a variant of the enum `T`, which is a hole."""
    if lw.lookup is None or lw.lookup.is_variant(str(ty), node.method):
        return lw.hole(node)
    ident = lw.made_opaque()
    return e.Call(ident, lw.span(node), e.Static(ty, node.method), _arguments(lw, node.args))


def _qualified_type(lw: BodyLowerer, receiver: Any) -> Optional[UnknownType]:
    """`ns.T` before the dot, when the namespace `ns` holds a type `T`; else None."""
    lookup = lw.lookup
    if lookup is None:
        return None

    def behind_alias(member: a.MemberAccess) -> Optional[UnknownType]:
        found = lw.bindings.resolution_of(member.receiver)
        if not (isinstance(found, bd.Namespace) and lookup.member_kind(
                lw.bindings.body, found.name, member.member) in _TYPE_KINDS):
            return None
        return UnknownType(member.member, namespace=found.name)

    walked = walk_place(receiver, Step.MEMBER, stop=behind_alias)
    return None if walked.path else walked.stop


def _written_type(name: str) -> BuiltinType | UnknownType:
    """The type that a type name spells, as the AST builder makes it from an annotation."""
    try:
        return BuiltinType(name)
    except ValueError:
        return UnknownType(name)


def _arguments(lw: BodyLowerer, args: list[Any]) -> list[e.Arg]:
    """Each argument in the written order, which is the order of evaluation."""
    return [_argument(lw, arg) for arg in args]


def _argument(lw: BodyLowerer, node: Any) -> e.Arg:
    row = ARGUMENTS.get(type(node))
    if row is not None:
        return row(lw, node)
    return e.Arg(lw.value(node), _marker("nom") if node.nom_marked else None)


def _marked(lw: BodyLowerer, node: a.Borrow) -> e.Arg:
    """`peek x` or `poke x`: the marker of the argument, not a `Borrow` (ir.md 7.4)."""
    return e.Arg(lw.expr(node.expr), _marker(node.mutability))


def _bloom(lw: BodyLowerer, node: a.Spread) -> e.Arg:
    """`xs...`: the array itself, forwarded whole."""
    return e.Arg(lw.expr(node.value), None, bloom=True)


def _marker(word: str) -> Convention:
    """The convention that a written marker gives: `nom`, `peek` or `poke`."""
    return receiver_mode(word).convention


# -- statements -------------------------------------------------------------------


def _expr_stmt(lw: BodyLowerer, node: a.ExprStmt) -> s.ExprStmt:
    ident = lw.ident()
    return s.ExprStmt(ident, lw.span(node), lw.expr(node.expr))


def _return(lw: BodyLowerer, node: a.Return) -> s.Return:
    ident = lw.ident()
    return s.Return(ident, lw.span(node), lw.expr(node.value))


def _print(lw: BodyLowerer, node: a.Print) -> s.ExprStmt:
    return _builtin_statement(lw, node, "print")


def _println(lw: BodyLowerer, node: a.PrintLn) -> s.ExprStmt:
    return _builtin_statement(lw, node, "println")


def _builtin_statement(lw: BodyLowerer, node: a.Print | a.PrintLn, name: str) -> s.ExprStmt:
    """A `print` statement is a call of a built-in function, with the value as its argument."""
    ident = lw.ident()
    call = e.Call(lw.made_opaque(), lw.span(node), e.BuiltinFn(name),
                  [_argument(lw, node.value)])
    return s.ExprStmt(ident, lw.span(node), call)


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
    a.Call: _call,
    a.DotCall: _dot_call,
    a.MethodCall: _method_call,
    a.ExprStmt: _expr_stmt,
    a.Return: _return,
    a.Print: _print,
    a.PrintLn: _println,
}

LowerArg: TypeAlias = Callable[[BodyLowerer, Any], e.Arg]

#: The AST classes that are the form of a call argument, with no value of their own there.
ARGUMENTS: dict[type, LowerArg] = {
    a.Borrow: _marked,
    a.Spread: _bloom,
}
