"""A SHIR body and its `TypeckResults`, made by hand (docs/design/ir.md 7.3).

The completeness check and the printer of the answers read this one sample. It is data:
no source text, no AST, no lowering and no pass. The body holds each node that a row of
the table is for, so that a table with no row of one kind leaves a gap that the check must
find. The check reads no type, so each instance type and each fact holds a stand-in type.

The body, in the source spelling that it stands for:

    fn sample(nom string name, bool loud) ~ | StdError:
        let string msg = decorate(nom name)??
        if (loud):
            shout(nom msg)
        let (a, (b, _)) = (1, (2, 3))
        foreach(i in 0..3):
            println("{i}!")
        match a as i64:
            0 -> ~
            _ -> ~
        Pair(a, b).first
        return Result.Ok(~)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sushi_lang.internals.report import Span
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import Instance, ShirBody, ShirLocal, ShirParam, ExpandCopies
from sushi_lang.semantics.shir.callee import Free, Intrinsic
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import IdAllocator, LocalId, ShirId
from sushi_lang.semantics.shir.loop_kind import CountedRange
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.shir.typeck import (
    MatchFacts, NoConversion, ResolvedCall, TryFacts, TypeckResults,
)
from sushi_lang.semantics.typesys import BuiltinType

I32 = BuiltinType.I32
I64 = BuiltinType.I64
BOOL = BuiltinType.BOOL
STRING = BuiltinType.STRING
BLANK = BuiltinType.BLANK

#: The type of each local, by its index. The parameters are `_0` and `_1`.
LOCAL_TYPES = {0: STRING, 1: BOOL, 2: STRING, 3: I32, 4: I32, 5: I32}


@dataclass(slots=True)
class Sample:
    """The body, the names of its ids, and the nodes that a row of the table is for."""
    body: ShirBody
    decls: DeclNames
    exprs: list[e.ExprNode] = field(default_factory=list)
    nodes: dict[str, object] = field(default_factory=dict)


def span(line: int) -> Span:
    return Span(line, 5, line, 9)


def sample() -> Sample:
    """The sample body. Each expression node is in `exprs`, in the order that it is made."""
    ids: IdAllocator[ShirId] = IdAllocator(ShirId)
    decls = DeclNames()
    fn_id = decls.declare("sample")
    made: list[e.ExprNode] = []
    named: dict[str, object] = {}

    def expr(name: str | None, cls: Callable[..., e.ExprNode], line: int, *args: object) -> e.ExprNode:
        node = cls(ids.allocate(), span(line), *args)
        made.append(node)
        if name is not None:
            named[name] = node
        return node

    def stmt(name: str, cls: Callable[..., object], line: int, *args: object) -> object:
        node = cls(ids.allocate(), span(line), *args)
        named[name] = node
        return node

    def block(line: int, *statements: object) -> ShirBlock:
        return ShirBlock(ids.allocate(), span(line), list(statements))

    def local(index: int) -> e.ExprNode:
        return expr(None, e.LocalRef, 1, LocalId(index))

    def lit(value: e.Constant, line: int, name: str | None = None) -> e.ExprNode:
        return expr(name, e.Lit, line, value)

    decorate = expr("decorate_call", e.Call, 2,
                    e.Named(expr(None, e.GlobalRef, 2, decls.refer("decorate"))),
                    [e.Arg(local(0), Convention.OWNED)])
    assign = stmt("assign", s.Assign, 2, local(2), expr("try", e.Try, 2, decorate), True)
    shout = expr("shout_call", e.Call, 4,
                 e.Named(expr(None, e.GlobalRef, 4, decls.refer("shout"))),
                 [e.Arg(local(2), Convention.OWNED)])
    branch = stmt("if", s.If, 3, local(1), block(4, stmt("shout", s.ExprStmt, 4, shout)), None)
    inner = expr("inner_tuple", e.TupleInit, 5,
                 [lit(e.IntConst(2), 5, "two"), lit(e.IntConst(3), 5, "three")])
    outer = expr("outer_tuple", e.TupleInit, 5, [lit(e.IntConst(1), 5, "one"), inner])
    destructure = stmt("destructure", s.Destructure, 5,
                       [LocalId(3), s.Nested([LocalId(4), s.Discard()])], outer)
    interp = expr("interp", e.Interp, 7, ["", local(5), "!"])
    println = expr("println_call", e.Call, 7, e.BuiltinFn("println"), [e.Arg(interp, None)])
    loop = stmt("loop", s.Loop, 6,
                s.Items(expr("range", e.Range, 6, lit(e.IntConst(0), 6, "start"),
                             lit(e.IntConst(3), 6, "end"), False, False), LocalId(5), s.Bare()),
                block(7, stmt("print", s.ExprStmt, 7, println)))
    arms = [p.Arm(p.Literal(ids.allocate(), span(9), e.IntConst(0)), block(9)),
            p.Arm(p.Wildcard(ids.allocate(), span(10)), block(10))]
    match = stmt("match", s.Match, 8, expr("cast", e.Cast, 8, local(3), I64), arms, False)
    pair = expr("struct", e.StructInit, 11, decls.refer("Pair"),
                [e.Arg(local(3), None), e.Arg(local(4), None)], (0, 1), None)
    access = stmt("access", s.ExprStmt, 11, expr("field", e.Field, 11, pair, "first"))
    ok = expr("enum", e.EnumInit, 12, decls.refer("Result"), "Ok",
              [e.Arg(lit(e.BlankConst(), 12), None)])
    ret = stmt("return", s.Return, 12, ok)

    params = [ShirParam(LocalId(0), STRING, Convention.OWNED, span(1), False),
              ShirParam(LocalId(1), BOOL, Convention.GUARANTEED, span(1), False)]
    locals_ = [ShirLocal(LocalId(0), "name", STRING, span(1)),
               ShirLocal(LocalId(1), "loud", BOOL, span(1)),
               ShirLocal(LocalId(2), "msg", STRING, span(2)),
               ShirLocal(LocalId(3), "a", None, span(5)),
               ShirLocal(LocalId(4), "b", None, span(5)),
               ShirLocal(LocalId(5), "i", None, span(6))]
    body = ShirBody(fn_id, Instance(), params, locals_,
                    block(1, assign, branch, destructure, loop, match, access, ret),
                    ExpandCopies())
    return Sample(body, decls, made, named)


def _id(sample: Sample, name: str) -> ShirId:
    node = sample.nodes[name]
    assert isinstance(node, (e.ExprNode, s.Destructure, s.Loop, s.Match))
    return node.shir_id


#: The label of each row that `answers` writes, one for each node or position.
ROWS = (
    "locals", "types",
    "callee decorate", "callee shout", "callee println",
    "convention decorate 0", "convention shout 0", "convention println 0",
    "instance inner_tuple", "instance outer_tuple", "instance struct", "instance enum",
    "field index", "loop kind", "range value",
    "conversion try", "conversion cast", "try facts",
    "display interp 1", "display println 0",
    "match facts",
    "element 0", "element 1", "element 1.0", "element 1.1",
    "value one", "value two", "value three", "value start", "value end",
)


def answers(sample: Sample, *, skip: str | None = None,
            skip_type: e.ExprNode | None = None,
            skip_local: int | None = None) -> TypeckResults:
    """The full table of the sample, less the row `skip`, the type of `skip_type` and the
    type of the local `skip_local`."""
    table = TypeckResults()

    def row(label: str, write: Callable[..., None], *args: object) -> None:
        assert label in ROWS, label
        if label != skip:
            write(*args)

    for index, ty in LOCAL_TYPES.items():
        if index != skip_local:
            row("locals", table.record_local, LocalId(index), ty)
    for node in sample.exprs:
        if node is not skip_type:
            row("types", table.record_type, node.shir_id, I32)
    calls = {"decorate": "decorate_call", "shout": "shout_call", "println": "println_call"}
    callees = {"decorate": Free(sample.decls.refer("decorate")),
               "shout": Free(sample.decls.refer("shout")),
               "println": Intrinsic("println")}
    conventions = {"decorate": Convention.OWNED, "shout": Convention.OWNED,
                   "println": Convention.GUARANTEED}
    for name, node in calls.items():
        row(f"callee {name}", table.record_callee, _id(sample, node),
            ResolvedCall(callees[name]))
        row(f"convention {name} 0", table.record_convention, _id(sample, node), 0,
            conventions[name])
    for name in ("inner_tuple", "outer_tuple", "struct", "enum"):
        row(f"instance {name}", table.record_instance, _id(sample, name), BLANK)
    row("field index", table.record_field_index, _id(sample, "field"), 0)
    row("loop kind", table.record_loop_kind, _id(sample, "loop"), CountedRange())
    row("range value", table.record_range_value, _id(sample, "range"), False)
    row("conversion try", table.record_conversion, _id(sample, "try"), NoConversion())
    row("conversion cast", table.record_conversion, _id(sample, "cast"), NoConversion())
    row("try facts", table.record_try, _id(sample, "try"),
        TryFacts(success_tag=0, ok=STRING, err=BLANK, returns=BLANK))
    row("display interp 1", table.record_display, _id(sample, "interp"), 1, I32)
    row("display println 0", table.record_display, _id(sample, "println_call"), 0, STRING)
    row("match facts", table.record_match, _id(sample, "match"), MatchFacts(I64, I64))
    for path, ty in (((0,), I32), ((1,), BLANK), ((1, 0), I32), ((1, 1), I32)):
        row(f"element {'.'.join(str(step) for step in path)}", table.record_element,
            _id(sample, "destructure"), path, ty)
    for name, held in (("one", 1), ("two", 2), ("three", 3), ("start", 0), ("end", 3)):
        row(f"value {name}", table.record_value, _id(sample, name), ScalarConstant(held, I32))
    return table
