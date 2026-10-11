"""Body lowering: each AST statement and expression gives one SHIR node (docs/design/ir.md 7.10).

ONE table, `LOWERING`, gives the lowering of each AST body class that lowering handles,
keyed on the class (gate: `tests/unit/test_shir_lowering_is_one_table.py`). A class with
no row is a HOLE: lowering makes a `Hole` that names the AST class, records it, and the
compile goes on through the old pipeline. When the variable that `STRICT_VARIABLE` names
is `1`, a hole stops the compile with the stop code CE0155 instead. A value that carries
the `nom` marker outside a call argument is a `Take` of it: the AST builder puts the marker
there only in a `let` initializer and in a `return` (ir.md 7.4).

A call argument has a second table, `ARGUMENTS`: the classes that are the FORM of an
argument there, and no value of their own. `nom x`, `peek x` and `poke x` are the written
marker of the `Arg`, and `xs...` is a bloom. The argument of a struct or a variant
construction is an `Arg` too. In any other position, `peek x` and `poke x` are a `Borrow`
(a reference `let` gives one too, from the same function). A pattern has a third table,
`PATTERNS` in `lower_patterns.py`, and the `Match` row lowers each arm through it. The
constructions, the member read, the index, the slice and the range are rows of
`LOWERING`, and `lower_aggregates.py` holds their lowering. A lambda and a destructure
declare locals; they are rows of `LOWERING` too, and `lower_binders.py` holds their
lowering. An `assert` keeps its message as an expression, because the message is
evaluated only when the condition is false (ir.md 7.5).

A name lowers to the answer that the scope pass recorded for it (ir.md 7.1): a local, a
constant, a unit `var` or a function. A name with no declaration (`Unresolved`), and a
type or a namespace written as a value, is a hole: the program has an error, and SHIR has
no node for such a name. The binder of a `let` gives a `ShirLocal`, with the `LocalId`
that the scope pass recorded for it. A binder with no name span is one that the AST
builder made: its local is hidden, with no name (ir.md 7.2).

Ruling E3 (ir.md 7.4): an operand whose value the typecheck pass reads is read here, with
the AST constant evaluator, and the value is recorded on its node (`readable`). These are
the divisor of `/` and `%`, the count of `<<` and `>>`, and each operand of the operators
that the overflow check folds (`+`, `-`, `*` and the negation). Lowering has no types, so
the evaluator reads at `i64`, the widest signed type; the typecheck pass applies the type
of the position. An operand is read only when it holds no opaque node: a local, a `var`, a
function, a call, a field read, a construction, a borrow, a take, a `??`, a slice, a
lambda or a hole has no value that the compiler can read. A cast and an interpolated string are not
opaque: the evaluator folds them with no write on the AST.

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
from sushi_lang.semantics.param_modes import Convention, borrow_mode, receiver_mode
from sushi_lang.semantics.places import Step, walk_place
from sushi_lang.semantics.shir import bindings as bd
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId, ShirId
from sushi_lang.semantics.shir.lookup import CALLABLE_KINDS, TYPE_KINDS, DeclLookup
from sushi_lang.semantics.shir.lower_aggregates import (
    enum_init, lower_array, lower_from, lower_index, lower_member, lower_new, lower_range,
    lower_tuple, struct_init,
)
from sushi_lang.semantics.shir.lower_binders import lower_destructure, lower_lambda
from sushi_lang.semantics.shir.lower_patterns import lower_match
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, TypeRef
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType, UnknownType

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
    value of an operand (ruling E3); with no evaluator, no value is recorded. `lookup`
    reads the declaration tables; with no lookup, a call on a type name or a namespace is
    a hole. `opaque` counts the nodes made so far whose value the compiler cannot read.
    `locals` holds the local of each binder that the body declares, in the order of the walk.
    """
    ids: IdAllocator[ShirId]
    decls: DeclNames
    bindings: bd.Bindings
    holes: list[Hole]
    fallback: Span
    evaluator: Optional[ConstantEvaluator] = None
    lookup: Optional[DeclLookup] = None
    opaque: int = field(default=0, init=False)
    locals: list[ShirLocal] = field(default_factory=list, init=False)

    def stmt(self, node: Any) -> s.ShirStmt:
        row = LOWERING.get(type(node))
        return self.hole(node) if row is None else cast(s.ShirStmt, row(self, node))

    def expr(self, node: Any) -> e.ShirExpr:
        """The expression `node` in the position of a value, where `nom` is a take."""
        return self.take(node) if node.nom_marked else self.value(node)

    def take(self, node: Any) -> e.Take:
        """`nom s.out` in a `let` or a `return`: a `Take` of the place as written.

        That the place is one step off a name that the body owns is a rule of the borrow
        pass, and lowering records the written shape.
        """
        ident = self.made_opaque()
        return e.Take(ident, self.span(node), self.value(node))

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

    def block(self, node: Optional[a.Block]) -> ShirBlock:
        """A block, each statement in the written order. No block gives an empty one."""
        ident = self.ident()
        statements = [] if node is None else [self.stmt(stmt) for stmt in node.statements]
        return ShirBlock(ident, self.span(node), statements)

    def initializer(self, decl: a.ConstDef) -> ShirBlock:
        """The block of a constant or a `var` initializer: one `Return` of the value (ir.md 6.3)."""
        return self.returned(decl.value, self.span(decl))

    def returned(self, value: Any, loc: Span) -> ShirBlock:
        """A block of one `Return` of `value`: an initializer, or the expression body of a lambda."""
        ident = self.ident()
        statement = s.Return(self.ident(), self.span(value), self.expr(value))
        return ShirBlock(ident, loc, [statement])

    def span(self, node: Any) -> Span:
        loc = getattr(node, "loc", None)
        return self.fallback if loc is None else loc

    def ident(self) -> ShirId:
        return self.ids.allocate()

    def binder(self, owner: object, name: str) -> LocalId:
        """The local that the scope pass recorded for the binder `name` of `owner`."""
        return binder_local(self.bindings, owner, name)

    def declare(self, local: LocalId, name: str, declared: Optional[TypeRef],
                name_span: Optional[Span]) -> None:
        """List the local of a binder. With no name span, the AST builder made it: hidden."""
        self.locals.append(ShirLocal(local, None if name_span is None else name, declared,
                                     name_span))

    def arguments(self, args: list[Any]) -> list[e.Arg]:
        """Each argument in the written order, which is the order of evaluation."""
        return _arguments(self, args)

    def type_before_dot(self, receiver: Any) -> Optional[BuiltinType | UnknownType]:
        """The type that `receiver` writes before a dot, or None when it is no type.

        A type name (`Sign`, `i32`), or a type behind an alias (`hm.HashMap`).
        """
        found = self.bindings.resolution_of(receiver)
        if isinstance(found, bd.TypeName):
            return _written_type(found.name)
        return _qualified_type(self, receiver)

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


def binder_local(bindings: bd.Bindings, owner: object, name: str) -> LocalId:
    """The local that the scope pass recorded for a binder. CE0153 when it recorded none."""
    local = bindings.local_of(owner, name)
    if local is None:
        er.raise_internal_error("CE0153", span=getattr(owner, "loc", None), what="binder",
                                name=name)
    return local


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


def _cast(lw: BodyLowerer, node: a.CastExpr) -> e.Cast:
    """`v as T`: the target type as written. A conversion is a `Cast` too (ir.md 4.8)."""
    ident = lw.ident()
    return e.Cast(ident, lw.span(node), lw.expr(node.expr), node.target_type)


# -- ownership and text -----------------------------------------------------------


def _borrow(lw: BodyLowerer, node: a.Borrow) -> e.Borrow:
    """`peek x` or `poke x` outside a call argument."""
    return _borrow_of(lw, lw.span(node), node.expr, borrow_mode(node.mutability))


def _borrow_of(lw: BodyLowerer, loc: Span, place: Any, mode: BorrowMode) -> e.Borrow:
    """The one `Borrow` of lowering: a marked value, and the value of a reference `let`."""
    ident = lw.made_opaque()
    return e.Borrow(ident, loc, lw.expr(place), mode)


def _try(lw: BodyLowerer, node: a.TryExpr) -> e.Try:
    """`v??`, kept whole: what it unwraps and converts is an answer of the typecheck pass."""
    ident = lw.made_opaque()
    return e.Try(ident, lw.span(node), lw.expr(node.expr))


def _interp(lw: BodyLowerer, node: a.InterpolatedString) -> e.Interp:
    """`"a{x}b"`: each text part as written, an empty one too, and each hole as an expression."""
    ident = lw.ident()
    return e.Interp(ident, lw.span(node), [part if isinstance(part, str) else lw.expr(part)
                                           for part in node.parts])


# -- calls ------------------------------------------------------------------------


def _call(lw: BodyLowerer, node: a.Call) -> e.ShirExpr:
    """`f(...)`: the callee is `Named`, a name or any expression that gives a function.

    A struct name as the callee is a construction (`StructInit`), and only a construction
    takes a named argument. SHIR has no field for written type arguments
    (`identity@(i32)(5)`), so such a call is a hole.
    """
    if node.type_args:
        return lw.hole(node)
    found = lw.bindings.resolution_of(node.callee)
    if isinstance(found, bd.TypeName):
        return struct_init(lw, node, found.name, found.name, node.args, node.field_names)
    if node.field_names is not None:
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

    A namespace gives what it holds: a function, or a struct to construct. A type name
    gives a variant or a static, also behind an alias (`hm.HashMap.new()`). Any other
    receiver is a value, and the target is `Dotted`: which method it calls is an answer of
    the typecheck pass (ir.md 7.4). A named argument outside a struct construction is a
    hole, as in `_call`.
    """
    found = lw.bindings.resolution_of(node.receiver)
    if isinstance(found, bd.Namespace):
        return _qualified_call(lw, node, found.name)
    if node.field_names is not None:
        return lw.hole(node)
    ty = lw.type_before_dot(node.receiver)
    if ty is not None:
        return _static_call(lw, node, ty)
    ident = lw.made_opaque()
    return e.Call(ident, lw.span(node), e.Dotted(lw.expr(node.receiver), node.method),
                  _arguments(lw, node.args))


def _qualified_call(lw: BodyLowerer, node: a.DotCall | a.MethodCall,
                    alias: str) -> e.ShirExpr:
    """`ns.f(...)`: the alias folds into the written name of the function that it holds.

    A struct that the namespace holds is a construction, named with the alias. Anything
    else that the namespace holds under the name is a hole.
    """
    kind = None if lw.lookup is None else lw.lookup.member_kind(lw.bindings.body, alias,
                                                                node.method)
    if kind == "struct":
        return struct_init(lw, node, f"{alias}.{node.method}", node.method, node.args,
                           node.field_names)
    if node.field_names is not None or kind not in CALLABLE_KINDS:
        return lw.hole(node)
    ident = lw.made_opaque()
    ref = e.GlobalRef(lw.made_opaque(), lw.span(node), lw.decls.refer(f"{alias}.{node.method}"))
    return e.Call(ident, lw.span(node), e.Named(ref), _arguments(lw, node.args))


def _static_call(lw: BodyLowerer, node: a.DotCall | a.MethodCall,
                 ty: BuiltinType | UnknownType) -> e.ShirExpr:
    """`T.m(...)`: a variant of the enum `T` (`EnumInit`), or a static method of `T`."""
    if lw.lookup is None:
        return lw.hole(node)
    if lw.lookup.is_variant(str(ty), node.method):
        return enum_init(lw, node, ty, node.method, node.args)
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
                lw.bindings.body, found.name, member.member) in TYPE_KINDS):
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


def _let(lw: BodyLowerer, node: a.Let) -> s.ShirStmt:
    """`let T x = v`: an `Assign` that initializes the local of the binder.

    The local keeps the type as written, or none. A binder that the AST builder made has no
    name span, and its local has no name (ir.md 7.2). A reference binding
    (`let poke T x = place`) takes a `Borrow` of the place, with the written mode (ir.md 7.5).
    A destructure (`let (a, b) = v`) is a `Destructure` (`lower_binders.py`).
    """
    if node.targets is not None:
        return lower_destructure(lw, node)
    ident = lw.ident()
    local = binder_local(lw.bindings, node, node.name)
    lw.declare(local, node.name, node.ty, node.name_span)
    target = e.LocalRef(lw.made_opaque(), node.name_span or lw.span(node), local)
    return s.Assign(ident, lw.span(node), target, _initial_value(lw, node), is_init=True)


def _initial_value(lw: BodyLowerer, node: a.Let) -> e.ShirExpr:
    if not isinstance(node.ty, ReferenceType):
        return lw.expr(node.value)
    return _borrow_of(lw, lw.span(node.value), node.value, node.ty.mutability)


def _rebind(lw: BodyLowerer, node: a.Rebind) -> s.Assign:
    """`place := v`: an `Assign` that frees the old value of the place (ir.md 4.6)."""
    ident = lw.ident()
    target = lw.expr(node.target)
    return s.Assign(ident, lw.span(node), target, lw.expr(node.value), is_init=False)


def _if(lw: BodyLowerer, node: a.If) -> s.If:
    return _arm(lw, node, 0)


def _arm(lw: BodyLowerer, node: a.If, index: int) -> s.If:
    """The arm `index` of an `if`. An `elif` is an `If` in the `otherwise` of the arm before it."""
    cond, block = node.arms[index]
    ident = lw.ident()
    loc = lw.span(node) if index == 0 else lw.span(cond)
    test = lw.expr(cond)
    then = lw.block(block)
    return s.If(ident, loc, test, then, _otherwise(lw, node, index + 1))


def _otherwise(lw: BodyLowerer, node: a.If, index: int) -> Optional[ShirBlock]:
    if index < len(node.arms):
        ident = lw.ident()
        return ShirBlock(ident, lw.span(node.arms[index][0]), [_arm(lw, node, index)])
    return None if node.else_block is None else lw.block(node.else_block)


def _while(lw: BodyLowerer, node: a.While) -> s.Loop:
    """`while (cond)`: a `Loop` with a `Cond` source (ir.md 7.5)."""
    ident = lw.ident()
    source = s.Cond(lw.expr(node.cond))
    return s.Loop(ident, lw.span(node), source, lw.block(node.body))


def _foreach(lw: BodyLowerer, node: a.Foreach) -> s.Loop:
    """`foreach (x in it)`: a `Loop` with an `Items` source (ir.md 7.5).

    The source holds what the source says: the iterable, the binder and its written mode.
    The walk kind is an answer of the typecheck pass (ir.md 7.7), and lowering records
    nothing about it. The `let` that the AST builder put at the top of the body of a `??`
    binder is the first statement of the body, and it lowers as every `let` does.
    """
    ident = lw.ident()
    iterable = lw.expr(node.iterable)
    source = s.Items(iterable, _loop_binder(lw, node), _item_mode(node.item_borrow))
    body = lw.block(node.body)
    if node.item_try_let is not None and node.item_type is not None:
        _write_type(lw, node.item_try_let, node.item_type)
    return s.Loop(ident, lw.span(node), source, body)


def _loop_binder(lw: BodyLowerer, node: a.Foreach) -> LocalId | s.Discard:
    """The binder of a loop: `Discard` for `_`, else its local.

    A binder that the AST builder made, for a `??` binder or a tuple binder, is a hidden
    local (ir.md 7.2). A type written on a `??` binder names the value of the `let`, not
    the item, so the hidden local has no type.
    """
    if node.item_discarded:
        return s.Discard()
    local = binder_local(lw.bindings, node, node.item_name)
    if node.item_try_let is not None or node.item_name_span is None:
        lw.locals.append(ShirLocal(local, None, None, None))
    else:
        lw.locals.append(ShirLocal(local, node.item_name, node.item_type, node.item_name_span))
    return local


def _write_type(lw: BodyLowerer, let: a.Let, ty: TypeRef) -> None:
    """The written type of a `??` binder, on the local of the `let` that unwraps the item."""
    local = binder_local(lw.bindings, let, let.name)
    for made in lw.locals:
        if made.id == local:
            made.declared = ty


def _item_mode(marker: Optional[str]) -> s.ItemMode:
    return s.Bare() if marker is None else s.Ref(borrow_mode(marker))


def _assert(lw: BodyLowerer, node: a.Assert) -> s.Assert:
    """`assert(cond, message)`. `label` is the file name that the AST builder gives the unit."""
    ident = lw.ident()
    cond = lw.expr(node.cond)
    message = None if node.message is None else lw.expr(node.message)
    return s.Assert(ident, lw.span(node), cond, message, node.source_label)


def _break(lw: BodyLowerer, node: a.Break) -> s.Break:
    return s.Break(lw.ident(), lw.span(node))


def _continue(lw: BodyLowerer, node: a.Continue) -> s.Continue:
    return s.Continue(lw.ident(), lw.span(node))


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
    a.CastExpr: _cast,
    a.Borrow: _borrow,
    a.TryExpr: _try,
    a.InterpolatedString: _interp,
    a.Call: _call,
    a.DotCall: _dot_call,
    a.MethodCall: _method_call,
    a.MemberAccess: lower_member,
    a.TupleLiteral: lower_tuple,
    a.ArrayLiteral: lower_array,
    a.DynamicArrayFrom: lower_from,
    a.DynamicArrayNew: lower_new,
    a.RangeExpr: lower_range,
    a.IndexAccess: lower_index,
    a.Lambda: lower_lambda,
    a.ExprStmt: _expr_stmt,
    a.Return: _return,
    a.Print: _print,
    a.PrintLn: _println,
    a.Let: _let,
    a.Rebind: _rebind,
    a.If: _if,
    a.While: _while,
    a.Foreach: _foreach,
    a.Match: lower_match,
    a.Assert: _assert,
    a.Break: _break,
    a.Continue: _continue,
}

LowerArg: TypeAlias = Callable[[BodyLowerer, Any], e.Arg]

#: The AST classes that are the form of a call argument, with no value of their own there.
ARGUMENTS: dict[type, LowerArg] = {
    a.Borrow: _marked,
    a.Spread: _bloom,
}
