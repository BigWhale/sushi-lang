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
- A list and a nested block print in brackets. A statement of the body block takes one
  line, and a body block with no statement prints `[]`.
- An argument with a written mode prints as `(value, Convention)`; a bloom adds `...`.
- An expression with a recorded value (ruling E3) prints the value after it: `... = 3`.
- No `shir_id` and no span: the example of ir.md 8.11 shows neither.
- A hole prints as `<hole: AstClass>`, with the name of the AST class that it stands for.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import body as b
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import DeclId, LocalId
from sushi_lang.semantics.shir.nodes import Hole, ShirBlock, TypeRef
from sushi_lang.semantics.typesys import BorrowMode

#: The names of ir.md 8.3.
CONVENTIONS: dict[Convention, str] = {
    Convention.OWNED: "Owned",
    Convention.GUARANTEED: "Guaranteed",
    Convention.IN_GUARANTEED: "InGuaranteed",
    Convention.INOUT: "Inout",
}

_BORROW_MODES: dict[BorrowMode, str] = {BorrowMode.PEEK: "Peek", BorrowMode.POKE: "Poke"}

_INT_PREFIXES: dict[int, Callable[[int], str]] = {16: hex, 8: oct, 2: bin}


@dataclass(frozen=True, slots=True)
class ShirPrinter:
    """The text of one value of a body. `decls` names each `DeclId`."""
    decls: DeclNames

    def show(self, value: object) -> str:
        row = PRINTERS.get(type(value))
        if row is None:
            er.raise_internal_error("CE0154", name=type(value).__name__)
        text = row(self, value)
        readable = value.readable if isinstance(value, e.ExprNode) else None
        return text if readable is None else f"{text} = {self.show(readable)}"

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


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _type(ty: TypeRef) -> str:
    return display_type(ty)


def _optional(name: str, text: str | None) -> tuple[str, ...]:
    return () if text is None else (f"{name}: {text}",)


# -- the parts of an expression ------------------------------------------------------


def _int(_pr: ShirPrinter, const: e.IntConst) -> str:
    if const.byte_spelling is not None:
        return const.byte_spelling
    spell = _INT_PREFIXES.get(const.radix)
    return str(const.value) if spell is None else spell(const.value)


def _arg(pr: ShirPrinter, arg: e.Arg) -> str:
    text = pr.show(arg.value) + ("..." if arg.bloom else "")
    return text if arg.marker is None else f"({text}, {CONVENTIONS[arg.marker]})"


def _lambda_param(_pr: ShirPrinter, param: e.LambdaParam) -> str:
    return f"({_local(param.local)}, {CONVENTIONS[param.convention]})"


def _interp_part(pr: ShirPrinter, part: str | e.ShirExpr) -> str:
    return _quoted(part) if isinstance(part, str) else pr.show(part)


def _lambda(pr: ShirPrinter, lam: e.Lambda) -> str:
    ret = None if lam.ret is None else _type(lam.ret)
    err = None if lam.err is None else _type(lam.err)
    return _node("Lambda", pr.each(lam.params), *_optional("ret", ret),
                 *_optional("err", err), pr.show(lam.body))


# -- the parts of a statement --------------------------------------------------------


def _if(pr: ShirPrinter, stmt: s.If) -> str:
    otherwise = None if stmt.otherwise is None else pr.show(stmt.otherwise)
    return _node("If", pr.show(stmt.cond), f"then: {pr.show(stmt.then)}",
                 *_optional("otherwise", otherwise))


def _assert(pr: ShirPrinter, stmt: s.Assert) -> str:
    message = None if stmt.message is None else pr.show(stmt.message)
    return _node("Assert", pr.show(stmt.cond), *_optional("message", message),
                 f"label: {_quoted(stmt.label)}")


# -- the origin of a body ------------------------------------------------------------


def _template_check(_pr: ShirPrinter, origin: b.TemplateCheck) -> str:
    return _node("TemplateCheck", _quoted(str(origin.template)))


PRINTERS: dict[type, Render] = {
    # constants
    e.IntConst: _int,
    e.FloatConst: lambda _pr, c: repr(c.value),
    e.BoolConst: lambda _pr, c: "true" if c.value else "false",
    e.BlankConst: lambda _pr, _c: "Blank",
    e.StrConst: lambda _pr, c: _quoted(c.value),
    # expressions
    e.Lit: lambda pr, n: _node("Lit", pr.show(n.value)),
    e.LocalRef: lambda _pr, n: _node("LocalRef", _local(n.local)),
    e.ConstRef: lambda pr, n: _node("ConstRef", pr.decl(n.const_id)),
    e.StorageRef: lambda pr, n: _node("StorageRef", pr.decl(n.decl)),
    e.GlobalRef: lambda pr, n: _node("GlobalRef", pr.decl(n.decl)),
    e.Field: lambda pr, n: _node("Field", pr.show(n.base), _quoted(n.name)),
    e.Index: lambda pr, n: _node("Index", pr.show(n.base), pr.show(n.index),
                                 _flag("unchecked", n.unchecked)),
    e.Slice: lambda pr, n: _node("Slice", pr.show(n.base), pr.show(n.start), pr.show(n.end),
                                 _flag("inclusive", n.inclusive)),
    e.Call: lambda pr, n: _node("Call", pr.show(n.target), pr.each(n.args)),
    e.Unary: lambda pr, n: _node("Unary", n.op, pr.show(n.operand)),
    e.Binary: lambda pr, n: _node("Binary", n.op, pr.show(n.lhs), pr.show(n.rhs)),
    e.Cast: lambda pr, n: _node("Cast", pr.show(n.operand), _type(n.target)),
    e.Borrow: lambda pr, n: _node("Borrow", pr.show(n.place), _BORROW_MODES[n.mode]),
    e.Take: lambda pr, n: _node("Take", pr.show(n.place)),
    e.Try: lambda pr, n: _node("Try", pr.show(n.inner)),
    e.Lambda: _lambda,
    e.StructInit: lambda pr, n: _node(
        "StructInit", pr.decl(n.decl), pr.each(n.fields),
        f"written_order: [{', '.join(str(i) for i in n.written_order)}]"),
    e.EnumInit: lambda pr, n: _node("EnumInit", pr.decl(n.decl), _quoted(n.variant),
                                    pr.each(n.payload)),
    e.TupleInit: lambda pr, n: _node("TupleInit", pr.each(n.parts)),
    e.ArrayInit: lambda pr, n: _node("ArrayInit", pr.show(n.kind), pr.each(n.runs)),
    e.Range: lambda pr, n: _node("Range", pr.show(n.start), pr.show(n.end),
                                 _flag("inclusive", n.inclusive), _flag("reverse", n.reverse)),
    e.Interp: lambda pr, n: _node(
        "Interp", "[" + ", ".join(_interp_part(pr, part) for part in n.parts) + "]"),
    # the parts of an expression
    e.Named: lambda pr, t: _node("Named", pr.show(t.ref)),
    e.Dotted: lambda pr, t: _node("Dotted", pr.show(t.recv), _quoted(t.name)),
    e.Static: lambda _pr, t: _node("Static", _type(t.ty), _quoted(t.name)),
    e.BuiltinFn: lambda _pr, t: _node("BuiltinFn", _quoted(t.name)),
    e.Arg: _arg,
    e.Fixed: lambda _pr, _k: "Fixed",
    e.Dynamic: lambda _pr, _k: "Dynamic",
    e.Single: lambda pr, r: _node("Single", pr.show(r.value)),
    e.Repeat: lambda pr, r: _node("Repeat", pr.show(r.value), pr.show(r.count)),
    e.RangeRun: lambda pr, r: _node("RangeRun", pr.show(r.range)),
    e.LambdaParam: _lambda_param,
    # the block, and a construct that lowering does not handle yet
    ShirBlock: lambda pr, blk: pr.each(blk.statements),
    Hole: lambda _pr, n: f"<hole: {n.ast_class}>",
    # statements
    s.Assign: lambda pr, n: _node("Assign", pr.show(n.target), pr.show(n.value),
                                  _flag("is_init", n.is_init)),
    s.Destructure: lambda pr, n: _node("Destructure", pr.each(n.targets), pr.show(n.value)),
    s.ExprStmt: lambda pr, n: _node("ExprStmt", pr.show(n.value)),
    s.Return: lambda pr, n: _node("Return", pr.show(n.value)),
    s.If: _if,
    s.Loop: lambda pr, n: _node("Loop", pr.show(n.source), pr.show(n.body)),
    s.Match: lambda pr, n: _node("Match", pr.show(n.scrutinee), pr.each(n.arms),
                                 _flag("consumes", n.consumes)),
    s.Assert: _assert,
    s.Break: lambda _pr, _n: "Break",
    s.Continue: lambda _pr, _n: "Continue",
    s.Expand: lambda pr, n: _node("Expand", _local(n.binder), _quoted(n.pack),
                                  pr.show(n.body)),
    # the parts of a statement
    LocalId: lambda _pr, local: _local(local),
    s.Discard: lambda _pr, _t: "_",
    s.Nested: lambda pr, t: pr.each(t.targets),
    s.Cond: lambda pr, src: _node("Cond", pr.show(src.cond)),
    s.Items: lambda pr, src: _node("Items", pr.show(src.iterable), _local(src.binder),
                                   pr.show(src.by)),
    s.Bare: lambda _pr, _m: "Bare",
    s.Ref: lambda _pr, m: _node("Ref", _BORROW_MODES[m.mode]),
    # patterns
    p.Wildcard: lambda _pr, _n: "Wildcard",
    p.Literal: lambda pr, n: _node("Literal", pr.show(n.value)),
    p.IntRange: lambda pr, n: _node("IntRange", pr.show(n.low), pr.show(n.high),
                                    _flag("inclusive", n.inclusive)),
    p.Variant: lambda pr, n: _node("Variant", pr.decl(n.decl), _quoted(n.variant),
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
}


# -- a body and a program ------------------------------------------------------------


def _named_local(local: b.ShirLocal | None, ident: LocalId, ty: TypeRef | None) -> str:
    name = None if local is None else local.name
    text = _local(ident) if name is None else f"{_local(ident)} {_quoted(name)}"
    return text if ty is None else f"{text}: {_type(ty)}"


def print_body(body: b.ShirBody, decls: DeclNames) -> str:
    """The text of one body: its header, its parameters, its other locals, its block.

    `locals` holds every local of the body, the parameters too (ir.md 7.2): the line of a
    parameter takes its name from there, and the `locals` line shows the others.
    """
    pr = ShirPrinter(decls)
    by_id = {local.id: local for local in body.locals}
    params = [param.local for param in body.params]
    origin = "" if isinstance(body.origin, b.Instance) else f" ({pr.show(body.origin)})"
    param_texts = (f"({_named_local(by_id.get(param.local), param.local, param.ty)}, "
                   f"{CONVENTIONS[param.convention]})" for param in body.params)
    local_texts = (_named_local(local, local.id, local.declared)
                   for local in sorted(body.locals, key=lambda local: local.id)
                   if local.id not in params)
    lines = [f"ShirBody {decls.name_of(body.fn_id)}{origin}",
             f"  params : [{', '.join(param_texts)}]",
             f"  locals : [{', '.join(local_texts)}]"]
    if body.block.statements:
        lines.append("  block  :")
        lines.extend(f"    {pr.show(stmt)}" for stmt in body.block.statements)
    else:
        lines.append("  block  : []")
    return "\n".join(lines) + "\n"


def print_units(units: Sequence[tuple[str, Sequence[b.ShirBody]]], decls: DeclNames) -> str:
    """The text of a program: each unit, by its name, then each of its bodies, in order."""
    parts: list[str] = []
    for name, bodies in units:
        parts.append(f"unit {name}\n")
        parts.extend(print_body(body, decls) for body in bodies)
    return "\n".join(parts)
