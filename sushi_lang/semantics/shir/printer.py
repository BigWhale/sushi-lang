"""The SHIR printer: the text of `--dump-shir` (docs/design/ir.md 8.11, 9 Q6, 11.3).

A pure function of the bodies. ONE table, `PRINTERS`, gives the text of each class that a
body holds (gate: `tests/unit/test_shir_printer_is_total.py`), and a class with no row is
the internal error CE0154. The text is the same on every run and under every hash seed:
the printer reads no address and no hash order, and it prints the ids that the
allocators gave, in id order.

The rules of the text, from the example of ir.md 8.11:

- A node prints as its kind, then its fields in the order of ir.md, in parentheses. A
  node with no field prints as its kind alone (`Break`).
- A local prints as `_<index>`, a declaration as `@<written name>`, a type in the source
  spelling (`display_type`), and a name or a text as a quoted string.
- A bool field prints with its name (`is_init: true`). An optional field that is empty
  is not printed; an optional field that is set prints with its name (`otherwise: [...]`).
- A list and a nested block print in brackets. A list of types prints each type in the
  source spelling (`type_args: [i32]`), and a list of names each name quoted. A statement of the body block takes one
  line, and a body block with no statement prints `[]`.
- An argument with a written mode prints as `(value, Convention)`; a bloom adds `...`.
- No `shir_id` and no span: the example of ir.md 8.11 shows neither.
- A hole prints as `<hole: AstClass>`, with the name of the AST class that it stands for.
- The answers of `typecheck` print in place, from the table of the body (ir.md 7.3): the
  resolved callee in place of the call target (`Free(@decorate)`, `Intrinsic("println")`),
  each argument of a call with its resolved convention, and the type of each local. No
  other answer of `typecheck` prints. Where the table holds no answer, the written form
  prints.
- The answers of `borrow` and `lift` print in place, from their tables (ir.md 7.9), where a
  row exists: the `Ownership` of a consuming use as a word before the node (`move`,
  `adopt`, `reject`, the name of the answer in lower case), the conditionally moved locals on a
  `cond_moved` line after the `locals` line, `iterator: lent` or `iterator: owned` and
  `heap: <bool>` after the arguments of an iterator call, `in_place: <bool>` after the body
  of a loop, and `symbol: "<symbol>"` and `env: [(<ownership> _<n>, Copy | Move), ...]`
  after the fields of a lambda. With no row, the text is the text of the written form.
- A value pack parameter (`...Ts args`) adds `pack: true` to its parameter.
- A body with copies of an unrolled `expand` prints a `copies` line after its block: the
  path of each block that holds a copy, from the block of the body (`block`,
  `block[6].body`), and the range `start..stop` of each copy. A body with no copy prints
  no such line.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.ownership import Ownership
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import body as b
from sushi_lang.semantics.shir import callee as c
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.shir.lift import Capture, LiftResults
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, TypeRef
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.shir.walk import walk
from sushi_lang.semantics.typesys import BorrowMode

#: The names of ir.md 8.3.
CONVENTIONS: dict[Convention, str] = {
    Convention.OWNED: "Owned",
    Convention.GUARANTEED: "Guaranteed",
    Convention.IN_GUARANTEED: "InGuaranteed",
    Convention.INOUT: "Inout",
}

_BORROW_MODES: dict[BorrowMode, str] = {BorrowMode.PEEK: "Peek", BorrowMode.POKE: "Poke"}

#: The word of each `Ownership` answer of a consuming use: its name, in lower case.
_OWNERSHIPS: dict[Ownership, str] = {
    Ownership.MOVE: "move",
    Ownership.ADOPT: "adopt",
    Ownership.REJECT: "reject",
}

@dataclass(frozen=True, slots=True)
class ShirPrinter:
    """The text of one value of a body. `decls` names each `DeclId`; `answers`,
    `ownership` and `lift` hold the answers of `typecheck`, `borrow` and `lift` for the
    body, if any."""
    decls: DeclNames
    answers: Optional[TypeckResults] = None
    ownership: Optional[OwnershipResults] = None
    lift: Optional[LiftResults] = None

    def show(self, value: object) -> str:
        """The text of `value`; a consuming use has the word of its `Ownership` before it."""
        row = PRINTERS.get(type(value))
        if row is None:
            er.raise_internal_error("CE0154", name=type(value).__name__)
        text = row(self, value)
        if self.ownership is None or not isinstance(value, e.ExprNode):
            return text
        decided = self.ownership.ownership_of(value.shir_id)
        return text if decided is None else f"{_OWNERSHIPS[decided]} {text}"

    def each(self, values: Iterable[object]) -> str:
        return "[" + ", ".join(self.show(value) for value in values) + "]"

    def decl(self, decl: DeclId) -> str:
        return "@" + self.decls.name_of(decl)


Render: TypeAlias = Callable[[ShirPrinter, Any], str]


def _node(kind: str, *fields: str) -> str:
    return f"{kind}({', '.join(fields)})" if fields else kind


def _local(local: LocalId) -> str:
    return f"_{local.index}"


def _flag(name: str, value: bool) -> str:
    return f"{name}: {'true' if value else 'false'}"


def quoted(text: str) -> str:
    """A name or a text as a quoted string. The SLIR printer quotes a string the same way."""
    return json.dumps(text, ensure_ascii=False)


def _type(ty: TypeRef) -> str:
    return display_type(ty)


def _optional(name: str, text: str | None) -> tuple[str, ...]:
    return () if text is None else (f"{name}: {text}",)


# -- the parts of an expression ------------------------------------------------------


def _int(_pr: ShirPrinter, const: e.IntConst) -> str:
    return const.spelling()


def _arg(pr: ShirPrinter, arg: e.Arg, convention: Optional[Convention] = None) -> str:
    """An argument with its resolved convention, else with its written marker, if any."""
    text = pr.show(arg.value) + ("..." if arg.bloom else "")
    mode = arg.marker if convention is None else convention
    return text if mode is None else f"({text}, {CONVENTIONS[mode]})"


def _call(pr: ShirPrinter, call: e.Call) -> str:
    """The resolved callee in place of the target, each argument with its convention, and
    the state of the iterator that the call makes."""
    answers = pr.answers
    resolved = None if answers is None else answers.callee_of(call.shir_id)
    target = pr.show(call.target if resolved is None else resolved.callee)
    args = (_arg(pr, arg, None if answers is None else answers.convention_of(call.shir_id, i))
            for i, arg in enumerate(call.args))
    return _node("Call", target, "[" + ", ".join(args) + "]", *_iterator_state(pr, call))


def _iterator_state(pr: ShirPrinter, call: e.Call) -> tuple[str, ...]:
    """LENT or OWNED for an iterator call, and the heap decision of a lent one."""
    if pr.ownership is None:
        return ()
    lent = pr.ownership.lent_of(call.shir_id)
    heap = pr.ownership.lent_on_heap(call.shir_id)
    return (*_optional("iterator", None if lent is None else "lent" if lent else "owned"),
            *_optional("heap", None if heap is None else "true" if heap else "false"))


def _in_place(pr: ShirPrinter, loop: s.Loop) -> tuple[str, ...]:
    walks = None if pr.ownership is None else pr.ownership.in_place_of(loop.shir_id)
    return () if walks is None else (_flag("in_place", walks),)


def _lambda_param(_pr: ShirPrinter, param: e.LambdaParam) -> str:
    return f"({_local(param.local)}, {CONVENTIONS[param.convention]})"


def _named(pr: ShirPrinter, target: e.Named) -> str:
    type_args = (f"[{', '.join(_type(ty) for ty in target.type_args)}]"
                 if target.type_args else None)
    return _node("Named", pr.show(target.ref), *_optional("type_args", type_args))


def _struct_init(pr: ShirPrinter, init: e.StructInit) -> str:
    names = (None if init.names is None
             else "[" + ", ".join(quoted(name) for name in init.names) + "]")
    return _node("StructInit", pr.decl(init.decl), pr.each(init.fields),
                 f"written_order: [{', '.join(str(i) for i in init.written_order)}]",
                 *_optional("names", names))


def _interp_part(pr: ShirPrinter, part: str | e.ShirExpr) -> str:
    return quoted(part) if isinstance(part, str) else pr.show(part)


def _lambda(pr: ShirPrinter, lam: e.Lambda) -> str:
    ret = None if lam.ret is None else _type(lam.ret)
    err = None if lam.err is None else _type(lam.err)
    return _node("Lambda", pr.each(lam.params), *_optional("ret", ret),
                 *_optional("err", err), pr.show(lam.body), _flag("expr_body", lam.expr_body),
                 *_lifted(pr, lam))


def _lifted(pr: ShirPrinter, lam: e.Lambda) -> tuple[str, ...]:
    """The symbol of the lifted function and its environment, each capture with its mode."""
    if pr.lift is None:
        return ()
    symbol = pr.lift.symbol_of(lam.shir_id)
    env = pr.lift.environment_of(lam.shir_id)
    slots = None if env is None else "[" + ", ".join(_capture(pr, lam, c) for c in env) + "]"
    return (*_optional("symbol", None if symbol is None else quoted(symbol)),
            *_optional("env", slots))


def _capture(pr: ShirPrinter, lam: e.Lambda, capture: Capture) -> str:
    decided = (None if pr.ownership is None
               else pr.ownership.capture_ownership_of(lam.shir_id, capture.local))
    local = _local(capture.local)
    text = local if decided is None else f"{_OWNERSHIPS[decided]} {local}"
    return f"({text}, {'Move' if capture.moves else 'Copy'})"


# -- the parts of a statement --------------------------------------------------------


def _if(pr: ShirPrinter, stmt: s.If) -> str:
    otherwise = None if stmt.otherwise is None else pr.show(stmt.otherwise)
    return _node("If", pr.show(stmt.cond), f"then: {pr.show(stmt.then)}",
                 *_optional("otherwise", otherwise))


def _assert(pr: ShirPrinter, stmt: s.Assert) -> str:
    message = None if stmt.message is None else pr.show(stmt.message)
    return _node("Assert", pr.show(stmt.cond), *_optional("message", message),
                 f"label: {quoted(stmt.label)}")


# -- the origin of a body ------------------------------------------------------------


def _template_check(_pr: ShirPrinter, origin: b.TemplateCheck) -> str:
    return _node("TemplateCheck", quoted(str(origin.template)))


PRINTERS: dict[type, Render] = {
    # constants
    e.IntConst: _int,
    e.FloatConst: lambda _pr, c: repr(c.value),
    e.BoolConst: lambda _pr, c: "true" if c.value else "false",
    e.BlankConst: lambda _pr, _c: "Blank",
    e.StrConst: lambda _pr, c: quoted(c.value),
    # expressions
    e.Lit: lambda pr, n: _node("Lit", pr.show(n.value)),
    e.LocalRef: lambda _pr, n: _node("LocalRef", _local(n.local)),
    e.ConstRef: lambda pr, n: _node("ConstRef", pr.decl(n.const_id)),
    e.StorageRef: lambda pr, n: _node("StorageRef", pr.decl(n.decl)),
    e.GlobalRef: lambda pr, n: _node("GlobalRef", pr.decl(n.decl)),
    e.Field: lambda pr, n: _node("Field", pr.show(n.base), quoted(n.name)),
    e.Index: lambda pr, n: _node("Index", pr.show(n.base), pr.show(n.index),
                                 _flag("unchecked", n.unchecked)),
    e.Slice: lambda pr, n: _node("Slice", pr.show(n.base), pr.show(n.start), pr.show(n.end),
                                 _flag("inclusive", n.inclusive)),
    e.Call: _call,
    e.Unary: lambda pr, n: _node("Unary", n.op, pr.show(n.operand)),
    e.Binary: lambda pr, n: _node("Binary", n.op, pr.show(n.lhs), pr.show(n.rhs)),
    e.Cast: lambda pr, n: _node("Cast", pr.show(n.operand), _type(n.target)),
    e.Borrow: lambda pr, n: _node("Borrow", pr.show(n.place), _BORROW_MODES[n.mode]),
    e.Take: lambda pr, n: _node("Take", pr.show(n.place)),
    e.Try: lambda pr, n: _node("Try", pr.show(n.inner)),
    e.Lambda: _lambda,
    e.StructInit: _struct_init,
    e.EnumInit: lambda pr, n: _node("EnumInit", pr.decl(n.decl), quoted(n.variant),
                                    pr.each(n.payload)),
    e.TupleInit: lambda pr, n: _node("TupleInit", pr.each(n.parts)),
    e.ArrayInit: lambda pr, n: _node("ArrayInit", pr.show(n.kind), pr.each(n.runs)),
    e.Range: lambda pr, n: _node("Range", pr.show(n.start), pr.show(n.end),
                                 _flag("inclusive", n.inclusive), _flag("reverse", n.reverse)),
    e.Interp: lambda pr, n: _node(
        "Interp", "[" + ", ".join(_interp_part(pr, part) for part in n.parts) + "]"),
    # the parts of an expression
    e.Named: _named,
    e.Dotted: lambda pr, t: _node("Dotted", pr.show(t.recv), quoted(t.name)),
    e.Static: lambda _pr, t: _node("Static", _type(t.ty), quoted(t.name)),
    e.BuiltinFn: lambda _pr, t: _node("BuiltinFn", quoted(t.name)),
    e.Arg: _arg,
    e.Fixed: lambda _pr, _k: "Fixed",
    e.Dynamic: lambda _pr, _k: "Dynamic",
    e.Single: lambda pr, r: _node("Single", pr.show(r.value)),
    e.Repeat: lambda pr, r: _node("Repeat", pr.show(r.value), pr.show(r.count)),
    e.RangeRun: lambda pr, r: _node("RangeRun", pr.show(r.range)),
    e.LambdaParam: _lambda_param,
    # the block, and a construct that lowering does not handle yet
    ShirBlock: lambda pr, blk: pr.each(blk.statements),
    Hole: lambda _pr, n: (f"<hole: {n.ast_class}, error: {' | '.join(n.codes)}>" if n.codes
                          else f"<hole: {n.ast_class}>"),
    # statements
    s.Assign: lambda pr, n: _node("Assign", pr.show(n.target), pr.show(n.value),
                                  _flag("is_init", n.is_init)),
    s.Destructure: lambda pr, n: _node("Destructure", pr.each(n.targets), pr.show(n.value)),
    s.ExprStmt: lambda pr, n: _node("ExprStmt", pr.show(n.value)),
    s.Return: lambda pr, n: _node("Return", pr.show(n.value)),
    s.If: _if,
    s.Loop: lambda pr, n: _node("Loop", pr.show(n.source), pr.show(n.body), *_in_place(pr, n)),
    s.Match: lambda pr, n: _node("Match", pr.show(n.scrutinee), pr.each(n.arms),
                                 _flag("consumes", n.consumes)),
    s.Assert: _assert,
    s.Break: lambda _pr, _n: "Break",
    s.Continue: lambda _pr, _n: "Continue",
    s.Expand: lambda pr, n: _node("Expand", _local(n.binder), quoted(n.pack),
                                  pr.show(n.body)),
    # the parts of a statement
    LocalId: lambda _pr, local: _local(local),
    s.Discard: lambda _pr, _t: "_",
    s.Nested: lambda pr, t: pr.each(t.targets),
    s.Place: lambda pr, t: _node("Place", pr.show(t.target)),
    s.Cond: lambda pr, src: _node("Cond", pr.show(src.cond)),
    s.Items: lambda pr, src: _node("Items", pr.show(src.iterable), pr.show(src.binder),
                                   pr.show(src.by)),
    s.Bare: lambda _pr, _m: "Bare",
    s.Ref: lambda _pr, m: _node("Ref", _BORROW_MODES[m.mode]),
    # patterns
    p.Wildcard: lambda _pr, _n: "Wildcard",
    p.Literal: lambda pr, n: _node("Literal", pr.show(n.value)),
    p.IntRange: lambda pr, n: _node("IntRange", pr.show(n.low), pr.show(n.high),
                                    _flag("inclusive", n.inclusive)),
    p.Variant: lambda pr, n: _node("Variant", pr.decl(n.decl), quoted(n.variant),
                                   pr.each(n.items)),
    p.Tuple: lambda pr, n: _node("Tuple", pr.each(n.elements)),
    p.Own: lambda pr, n: _node("Own", pr.show(n.inner)),
    p.Or: lambda pr, n: _node("Or", pr.each(n.alternatives)),
    # the parts of a pattern
    p.PatBinding: lambda pr, bnd: f"({_local(bnd.local)}, {pr.show(bnd.by)})",
    p.Nom: lambda _pr, _m: "Nom",
    p.Arm: lambda pr, arm: _node("Arm", pr.show(arm.pattern), pr.show(arm.body)),
    # the origin of a body
    b.Instance: lambda _pr, _o: "Instance",
    b.TemplateCheck: _template_check,
    b.Initializer: lambda _pr, _o: "Initializer",
    # the side table of a body
    b.CopyRange: lambda _pr, r: f"{r.start}..{r.stop}",
    # the callee that typecheck answers for a call (ir.md 7.6)
    c.Free: lambda pr, n: _node("Free", pr.decl(n.decl)),
    c.Method: lambda pr, n: _node("Method", pr.show(n.recv), pr.decl(n.decl)),
    c.Static: lambda pr, n: _node("Static", _type(n.ty), pr.decl(n.decl)),
    c.Builtin: lambda pr, n: _node("Builtin", pr.show(n.recv), pr.show(n.method)),
    c.BuiltinStatic: lambda pr, n: _node("BuiltinStatic", _type(n.ty), pr.show(n.method)),
    c.Conversion: lambda pr, n: _node("Conversion", pr.decl(n.decl)),
    c.Extern: lambda pr, n: _node("Extern", pr.decl(n.decl)),
    c.Intrinsic: lambda _pr, n: _node("Intrinsic", quoted(n.name)),
    c.Indirect: lambda pr, n: _node("Indirect", pr.show(n.value)),
    c.BuiltinMethod: lambda _pr, m: quoted(m.name),
}


# -- a body and a program ------------------------------------------------------------


def _named_local(local: b.ShirLocal | None, ident: LocalId, ty: TypeRef | None) -> str:
    name = None if local is None else local.name
    text = _local(ident) if name is None else f"{_local(ident)} {quoted(name)}"
    return text if ty is None else f"{text}: {_type(ty)}"


def print_body(body: b.ShirBody, decls: DeclNames, answers: Optional[TypeckResults] = None,
               *, ownership: Optional[OwnershipResults] = None,
               lift: Optional[LiftResults] = None) -> str:
    """The text of one body: its header, its parameters, its other locals, its block.

    `locals` holds every local of the body, the parameters too (ir.md 7.2): the line of a
    parameter takes its name from there, and the `locals` line shows the others. The type
    of a local is the answer of `typecheck` in `answers`, else the type as written. The
    conditionally moved locals of `ownership`, if any, follow on their own line.
    """
    pr = ShirPrinter(decls, answers, ownership, lift)

    def typed(ident: LocalId, written: Optional[TypeRef]) -> Optional[TypeRef]:
        answer = None if answers is None else answers.local_type(ident)
        return written if answer is None else answer

    by_id = {local.id: local for local in body.locals}
    params = [param.local for param in body.params]
    origin = "" if isinstance(body.origin, b.Instance) else f" ({pr.show(body.origin)})"
    def param_text(param: b.ShirParam) -> str:
        head = _named_local(by_id.get(param.local), param.local, typed(param.local, param.ty))
        return f"({head}, {CONVENTIONS[param.convention]}{', pack: true' if param.pack else ''})"

    param_texts = (param_text(param) for param in body.params)
    local_texts = (_named_local(local, local.id, typed(local.id, local.declared))
                   for local in sorted(body.locals, key=lambda local: local.id)
                   if local.id not in params)
    lines = [f"ShirBody {decls.name_of(body.fn_id)}{origin}",
             f"  params : [{', '.join(param_texts)}]",
             f"  locals : [{', '.join(local_texts)}]"]
    moved = () if ownership is None else ownership.conditionally_moved()
    if moved:
        lines.append(f"  cond_moved : [{', '.join(_local(local) for local in moved)}]")
    if body.block.statements:
        lines.append("  block  :")
        lines.extend(f"    {pr.show(stmt)}" for stmt in body.block.statements)
    else:
        lines.append("  block  : []")
    if body.copies.blocks():
        lines.append(f"  copies : {_copies(pr, body)}")
    return "\n".join(lines) + "\n"


def _copies(pr: ShirPrinter, body: b.ShirBody) -> str:
    """Each block that holds a copy of an unrolled `expand`, by its path, with its ranges."""
    paths: dict[ShirId, str] = {value.shir_id: path for path, value in walk(body.block, "block")
                                if isinstance(value, ShirBlock)}
    return "[" + ", ".join(f"{paths[block]}: {pr.each(body.copies.ranges_of(block))}"
                           for block in body.copies.blocks()) + "]"


#: A body, and the answers of `typecheck`, `borrow` and `lift` for it, if any.
AnsweredBody: TypeAlias = tuple[b.ShirBody, Optional[TypeckResults], Optional[OwnershipResults],
                                Optional[LiftResults]]


def print_units(units: Sequence[tuple[str, Sequence[AnsweredBody]]], decls: DeclNames) -> str:
    """The text of a program: each unit, by its name, then each of its bodies, in order."""
    parts: list[str] = []
    for name, bodies in units:
        parts.append(f"unit {name}\n")
        parts.extend(print_body(body, decls, answers, ownership=ownership, lift=lift)
                     for body, answers, ownership, lift in bodies)
    return "\n".join(parts)
