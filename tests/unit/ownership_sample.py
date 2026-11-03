"""A SHIR body with its `OwnershipResults` and its `LiftResults`, made by hand (docs/design/ir.md 7.9).

The completeness checks of the two tables and their printer read this one sample. It is
data: no source text, no AST, no lowering and no pass. The body holds each consuming
position, a position of each kind that is not consuming, an iterator producer and a lazy
adapter, the loops that need a walk-in-place answer and a loop that needs none, and a
lambda. The `TypeckResults` holds only the rows that the ownership check reads: the
conventions of the arguments, the callees of the iterator calls and the loop kinds.

The body, in the source spelling that it stands for:

    fn sample(nom string s, Box b, i32[] xs, poke Lines it, i32 k) ~ | AppError:
        let string t = s
        t := keep(nom t, 1, xs...)
        let (u, _) = (t, 2)
        let i32[] ys = [7; 3, 8]
        Box(Maybe.Some(9))
        match nom b:
            _ -> ~
        let AppError e = fail() as AppError
        k as i64
        foreach(x in xs.iter()):
            ~
        foreach(y in xs.iter().map(double)):
            ~
        foreach(line in it):
            ~
        let fn(i32) -> i32 g = |i32 v| v + k
        return Result.Ok(~)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sushi_lang.internals.report import Span
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ExpandCopies, Instance, ShirBody, ShirLocal, ShirParam
from sushi_lang.semantics.shir.callee import Builtin, BuiltinMethod, Free
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId, ShirId
from sushi_lang.semantics.shir.lift import Capture, LiftResults
from sushi_lang.semantics.shir.loop_kind import Cursor, IteratorValue, Protocol
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType, UnknownType

STRING = BuiltinType.STRING
I32 = BuiltinType.I32
I64 = BuiltinType.I64

#: The types of the five parameters, `s`, `b`, `xs`, `it` and `k`.
PARAM_TYPES = (STRING, UnknownType("Box"), DynamicArrayType(I32), UnknownType("Lines"), I32)

#: Each consuming use of the sample by its name, with the line of its node.
CONSUMED: dict[str, int] = {
    "s_ref": 2, "keep_call": 3, "t_arg": 3, "xs_bloom": 3, "pair": 4, "t_part": 4,
    "two": 4, "array": 5, "eight": 5, "some": 6, "nine": 6, "b_ref": 7,
    "conversion": 8, "fail_call": 8, "k_ref": 9, "map_call": 12, "double_ref": 12, "lambda": 15,
    "sum": 15, "ok": 16, "blank": 16,
}

#: The lambda captures `k`.
CAPTURED = LocalId(4)

SYMBOL = "sample$lambda_0"


@dataclass(slots=True)
class Sample:
    """The body, the names of its ids, its `TypeckResults`, and its named nodes."""
    body: ShirBody
    decls: DeclNames
    typeck: TypeckResults
    nodes: dict[str, e.ExprNode | s.Loop] = field(default_factory=dict)

    def id_of(self, name: str) -> ShirId:
        return self.nodes[name].shir_id


def span(line: int) -> Span:
    return Span(line, 5, line, 9)


def sample() -> Sample:
    ids: IdAllocator[ShirId] = IdAllocator(ShirId)
    decls = DeclNames()
    fn_id = decls.declare("sample")
    named: dict[str, e.ExprNode | s.Loop] = {}

    def expr(name: str | None, cls: Callable[..., e.ExprNode], line: int,
             *args: object) -> e.ExprNode:
        node = cls(ids.allocate(), span(line), *args)
        if name is not None:
            named[name] = node
        return node

    def stmt(cls: Callable[..., object], line: int, *args: object) -> object:
        return cls(ids.allocate(), span(line), *args)

    def block(line: int, *statements: object) -> ShirBlock:
        return ShirBlock(ids.allocate(), span(line), list(statements))

    def local(name: str | None, index: int, line: int) -> e.ExprNode:
        return expr(name, e.LocalRef, line, LocalId(index))

    def lit(name: str | None, value: e.Constant, line: int) -> e.ExprNode:
        return expr(name, e.Lit, line, value)

    def call(name: str, line: int, target: e.CallTarget, *args: e.Arg) -> e.ExprNode:
        return expr(name, e.Call, line, target, list(args))

    def iter_call(name: str, line: int) -> e.ExprNode:
        return call(name, line, e.Dotted(local(None, 2, line), "iter"))

    def loop(name: str, line: int, iterable: e.ExprNode, binder: int) -> s.Loop:
        node = stmt(s.Loop, line, s.Items(iterable, LocalId(binder), s.Bare()), block(line))
        assert isinstance(node, s.Loop)
        named[name] = node
        return node

    keep = call("keep_call", 3, e.Named(expr(None, e.GlobalRef, 3, decls.refer("keep"))),
                e.Arg(local("t_arg", 5, 3), Convention.OWNED),
                e.Arg(lit("one", e.IntConst(1), 3), None),
                e.Arg(local("xs_bloom", 2, 3), None, bloom=True))
    fail = call("fail_call", 8, e.Named(expr(None, e.GlobalRef, 8, decls.refer("fail"))))
    mapped = call("map_call", 12, e.Dotted(iter_call("producer", 12), "map"),
                  e.Arg(expr("double_ref", e.GlobalRef, 12, decls.refer("double")), None))
    lam = expr("lambda", e.Lambda, 15, [e.LambdaParam(LocalId(13), Convention.GUARANTEED)],
               None, None,
               block(15, stmt(s.Return, 15, expr("sum", e.Binary, 15, "+", local(None, 13, 15),
                                                 local(None, 4, 15)))),
               True)
    statements = [
        stmt(s.Assign, 2, local(None, 5, 2), local("s_ref", 0, 2), True),
        stmt(s.Assign, 3, local(None, 5, 3), keep, False),
        stmt(s.Destructure, 4, [LocalId(6), s.Discard()],
             expr("pair", e.TupleInit, 4, [local("t_part", 5, 4), lit("two", e.IntConst(2), 4)])),
        stmt(s.Assign, 5, local(None, 7, 5),
             expr("array", e.ArrayInit, 5, e.Fixed(),
                  [e.Repeat(lit("seven", e.IntConst(7), 5), lit("three", e.IntConst(3), 5)),
                   e.Single(lit("eight", e.IntConst(8), 5))]), True),
        stmt(s.ExprStmt, 6,
             expr("box", e.StructInit, 6, decls.refer("Box"),
                  [e.Arg(expr("some", e.EnumInit, 6, decls.refer("Maybe"), "Some",
                              [e.Arg(lit("nine", e.IntConst(9), 6), None)], span(6)),
                         None)],
                  (0,), None, False, span(6))),
        stmt(s.Match, 7, local("b_ref", 1, 7),
             [p.Arm(p.Wildcard(ids.allocate(), span(8)), block(8))], True),
        stmt(s.Assign, 8, local(None, 8, 8),
             expr("conversion", e.Cast, 8, fail, UnknownType("AppError")), True),
        stmt(s.ExprStmt, 9, expr("widen", e.Cast, 9, local("k_ref", 4, 9), I64)),
        loop("cursor_loop", 10, iter_call("cursor_iter", 10), 9),
        loop("value_loop", 12, mapped, 10),
        loop("protocol_loop", 14, local("it_ref", 3, 14), 11),
        stmt(s.Assign, 15, local(None, 12, 15), lam, True),
        stmt(s.Return, 16, expr("ok", e.EnumInit, 16, decls.refer("Result"), "Ok",
                                [e.Arg(lit("blank", e.BlankConst(), 16), None)], span(16))),
    ]
    conventions = [Convention.OWNED, Convention.GUARANTEED, Convention.GUARANTEED,
                   Convention.INOUT, Convention.GUARANTEED]
    params = [ShirParam(LocalId(index), ty, convention, span(1), False)
              for index, (ty, convention) in enumerate(zip(PARAM_TYPES, conventions, strict=True))]
    names = ["s", "b", "xs", "it", "k", "t", "u", "ys", "e", "x", "y", "line", "g", "v"]
    locals_ = [ShirLocal(LocalId(index), name, None, span(1)) for index, name in enumerate(names)]
    body = ShirBody(fn_id, Instance(), params, locals_, block(1, *statements), ExpandCopies())
    return Sample(body, decls, _typeck(decls, named), named)


def _typeck(decls: DeclNames, named: dict[str, e.ExprNode | s.Loop]) -> TypeckResults:
    """The rows of `TypeckResults` that the ownership check reads."""
    table = TypeckResults()
    conventions = {"keep_call": (Convention.OWNED, Convention.GUARANTEED, Convention.GUARANTEED),
                   "map_call": (Convention.OWNED,)}
    for name, answers in conventions.items():
        for position, convention in enumerate(answers):
            table.record_convention(named[name].shir_id, position, convention)
    xs = e.LocalRef(ShirId(99), span(1), LocalId(2))
    producer = named["producer"]
    assert isinstance(producer, e.Call)
    for name in ("producer", "cursor_iter"):
        table.record_callee(named[name].shir_id,
                            ResolvedCall(Builtin(xs, BuiltinMethod("array", "iter", nom_self=False))))
    table.record_callee(named["map_call"].shir_id,
                        ResolvedCall(Builtin(producer, BuiltinMethod("iterator", "map", nom_self=False))))
    table.record_callee(named["fail_call"].shir_id, ResolvedCall(Free(decls.refer("fail"))))
    table.record_loop_kind(named["cursor_loop"].shir_id, Cursor())
    table.record_loop_kind(named["value_loop"].shir_id, IteratorValue())
    table.record_loop_kind(named["protocol_loop"].shir_id, Protocol(Free(decls.refer("next"))))
    return table


#: The label of each row that `ownership` writes, beside one row for each consuming use.
OWNERSHIP_ROWS = (
    "capture", "lent producer", "lent map_call", "heap producer", "heap map_call",
    "in place value_loop", "in place protocol_loop",
)


def ownership(made: Sample, *, skip: str | None = None,
              reject: str | None = None) -> OwnershipResults:
    """The full table of the sample, less the row `skip`: the name of a consuming use or a
    label of `OWNERSHIP_ROWS`. The consuming use `reject` answers REJECT. The local `t` is
    conditionally moved."""
    table = OwnershipResults()
    lam = made.id_of("lambda")
    for name in CONSUMED:
        if name != skip:
            table.record_ownership(made.id_of(name), Ownership.REJECT if name == reject
                                   else Ownership.MOVE if name == "s_ref"
                                   else Ownership.ADOPT)
    rows: dict[str, Callable[[], None]] = {
        "capture": lambda: table.record_capture_ownership(lam, CAPTURED, Ownership.ADOPT),
        "lent producer": lambda: table.record_lent(made.id_of("producer"), True),
        "lent map_call": lambda: table.record_lent(made.id_of("map_call"), True),
        "heap producer": lambda: table.record_lent_on_heap(made.id_of("producer"), False),
        "heap map_call": lambda: table.record_lent_on_heap(made.id_of("map_call"), True),
        "in place value_loop": lambda: table.record_in_place(made.id_of("value_loop"), False),
        "in place protocol_loop": lambda: table.record_in_place(made.id_of("protocol_loop"), True),
    }
    for label, write in rows.items():
        if label != skip:
            write()
    table.record_conditional_move(LocalId(5))
    return table


def lift(made: Sample, *, skip: str | None = None) -> LiftResults:
    """The full table of the sample, less the row `skip` (`symbol` or `environment`)."""
    table = LiftResults()
    lam = made.id_of("lambda")
    if skip != "symbol":
        table.record_symbol(lam, SYMBOL)
    if skip != "environment":
        table.record_environment(lam, (Capture(CAPTURED, moves=False),))
    return table
