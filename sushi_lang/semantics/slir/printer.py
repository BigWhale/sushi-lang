"""The SLIR printer: the text of `--dump-slir` (docs/design/ir.md 8.11, 9 Q6, 11.3).

A pure function of the functions. ONE table, `PRINTERS`, gives the text of each class that
a function holds (gate: `tests/unit/test_slir_printer.py`), and a class with no row is an
internal error. The text is the same on every run and under every hash seed: the printer
reads no address and no hash order, and it prints the locals and the blocks in id order.

The rules of the text are L1 to L17 of the migration plan, and the golden
`tests/ir/spine/greet/test_spine_greet.slir` is their contract:

- A `unit <name>` line, then each function, with one blank line before each. A function
  is a header `fn <name>(<param>: <type> @<convention>, ...) -> <type> {`, one
  `let _N: <type>` line for the return slot and for each local that is not a parameter,
  then each block, and `}`. A view local adds `view` after its type, and a local with a
  source name adds `// <name>` at column 43.
- A block is `  bbN:`, then its statements and its terminator at four spaces, with one
  blank line before it.
- A function whose lowering stopped is one line: `fn <name>: stopped (slir, <construct>)`.
- An operand is `copy <place>`, `move <place>` or `const <constant>`. A place with a
  projection is in parentheses: `(_3.0)`, `(_3[copy _4])`, `((_3 as Ok).0)`, `(*_2)`. A
  unit-level `var` is `@<symbol>`.
- A constant: `true`, `false`, `~`, a quoted string, a number with `_` and its type
  (`0_i32`, `1.5_f64`), an aggregate as its type and its parts (`i32[3](2_i32, 3_i32)`),
  and a function as `fn <symbol>`.
- A callee: `@<name>` (the source name), `builtin <name>@(<types>)`, where the name of a
  row of `METHOD_TYPE_REGISTRY` is `<family>.<method>`, and `indirect(<operand>)`.
- An rvalue: an operand alone; `<a> <op> <b>`; `<op> <a>`; `<a> as <type>`;
  `discriminant(<place>)`, `clone(<operand>)`, `len(<place>)`, `slice(<place>, <a>, <b>)`,
  `ref peek <place>` and `ref poke <place>`. An aggregate of an enum variant is
  `<Enum>::<Variant>(<parts>)` (no parentheses with no part), of a struct
  `<type>(<parts>)`, of a tuple `(<parts>)`, of an array `[<parts>]`, of a closure
  `closure(<parts>)`, and of an iterator `iterator(<parts>)`.
- A statement: `<place> = <rvalue>`, `<place> = call <callee>(<operand> @<convention>, ...)`,
  `Drop(<place>)`, `DropIfSet(<place>, _F)`, `BeginBorrow(bN, <place>, peek|poke)` and
  `BeginBorrow(bN, <place>, peek|poke, for _V)` for a region that names its view local,
  `EndBorrow(bN)`, `StorageLive(_N)`, `StorageDead(_N)`.
- A terminator: `goto -> bbN`, `switchInt(<operand>) -> [<value>: bbN, ..., otherwise: bbN]`
  with `otherwise: unreachable` when no other value can reach it, `return`,
  `trap <code>(<operands>)` (no parentheses with no operand), and `unreachable`.
- No span prints.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence, TypeAlias

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.printer import quoted
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.function import (
    STAGE, SlirFunction, SlirLocal, SlirOutcome, SlirStopped,
)
from sushi_lang.semantics.slir.ids import BlockId, BorrowId, LocalId
from sushi_lang.semantics.type_predicates import generic_base_of
from sushi_lang.semantics.typesys import ArrayType, BorrowMode, Type

#: The conventions of ir.md 8.3, as the header and an argument write them.
CONVENTIONS: dict[Convention, str] = {
    Convention.OWNED: "owned",
    Convention.GUARANTEED: "guaranteed",
    Convention.IN_GUARANTEED: "in_guaranteed",
    Convention.INOUT: "inout",
}

_MODES: dict[BorrowMode, str] = {BorrowMode.PEEK: "peek", BorrowMode.POKE: "poke"}

#: The column of the `//` before the source name of a local (L3).
NAME_COLUMN = 43

Render: TypeAlias = Callable[[Any], str]


def show(value: object) -> str:
    """The text of one SLIR value."""
    row = PRINTERS.get(type(value))
    if row is None:
        er.raise_internal_error(
            "CE0000", detail=f"the SLIR printer has no row for the class '{type(value).__name__}'")
    return row(value)


def _each(values: Sequence[object]) -> str:
    return ", ".join(show(value) for value in values)


def _type(ty: Type) -> str:
    return display_type(ty)


def _local(ident: LocalId) -> str:
    return f"_{ident.index}"


def _block(ident: BlockId) -> str:
    return f"bb{ident.index}"


def _borrow(ident: BorrowId) -> str:
    return f"b{ident.index}"


def _arg(arg: tuple[p.Operand, Convention]) -> str:
    operand, convention = arg
    return f"{show(operand)} @{CONVENTIONS[convention]}"


def _builtin(callee: st.Builtin) -> str:
    types = f"@({', '.join(_type(ty) for ty in callee.types)})" if callee.types else ""
    return f"builtin {show(callee.method)}{types}"


def _variant(ty: Type, variant: str, parts: Sequence[object]) -> str:
    head = f"{generic_base_of(ty) or _type(ty)}::{variant}"
    return f"{head}({_each(parts)})" if parts else head


def _constant_aggregate(c: k.Aggregate) -> str:
    if c.variant is not None:
        return _variant(c.ty, c.variant, c.parts)
    if isinstance(c.ty, ArrayType):
        return f"[{_each(c.parts)}]"
    return f"{_type(c.ty)}({_each(c.parts)})"


def _aggregate(agg: r.Aggregate) -> str:
    if isinstance(agg.kind, r.AggVariant):
        return _variant(agg.kind.ty, agg.kind.variant, agg.parts)
    if isinstance(agg.kind, r.AggArray):
        return f"[{', '.join(_runs(agg.kind.runs, agg.parts))}]"
    return f"{show(agg.kind)}({_each(agg.parts)})"


def _runs(runs: Sequence[r.ArrayRun], parts: Sequence[object]) -> list[str]:
    """Each run of an array: `v`, `v; n`, or `seq(first, step; n)` (ir.md 8.7)."""
    texts, at = [], 0
    for run in runs:
        width = r.run_width(run)
        shown = [show(part) for part in parts[at:at + width]]
        at += width
        if isinstance(run, r.RunOne):
            texts.append(shown[0])
        elif isinstance(run, r.RunRepeat):
            texts.append(f"{shown[0]}; {shown[1]}")
        else:
            texts.append(f"seq({shown[0]}, {run.step:+d}; {shown[1]})")
    return texts


def _switch(term: tm.SwitchInt) -> str:
    otherwise = "unreachable" if term.otherwise is None else _block(term.otherwise)
    arms = [f"{value}: {_block(target)}" for value, target in term.targets]
    return f"switchInt({show(term.operand)}) -> [{', '.join([*arms, f'otherwise: {otherwise}'])}]"


def _trap(term: tm.Trap) -> str:
    return f"trap {term.code}({_each(term.args)})" if term.args else f"trap {term.code}"


PRINTERS: dict[type, Render] = {
    # places and operands (ir.md 8.4)
    p.Local: lambda n: _local(n.id),
    p.Global: lambda n: f"@{n.symbol}",
    p.Field: lambda n: f"({show(n.base)}.{n.index})",
    p.Capture: lambda n: f"({show(n.base)}.{n.index})",
    p.Index: lambda n: f"({show(n.base)}[{show(n.index)}])",
    p.Downcast: lambda n: f"({show(n.base)} as {n.variant})",
    p.Deref: lambda n: f"(*{show(n.base)})",
    p.Copy: lambda n: f"copy {show(n.place)}",
    p.Move: lambda n: f"move {show(n.place)}",
    p.Const: lambda n: f"const {show(n.constant)}",
    # constants (ir.md 8.5)
    k.Int: lambda c: f"{c.value}_{_type(c.ty)}",
    k.Float: lambda c: f"{c.value!r}_{_type(c.ty)}",
    k.Bool: lambda c: "true" if c.value else "false",
    k.Blank: lambda _c: "~",
    k.Str: lambda c: quoted(c.value.decode("utf-8", "surrogatepass")),
    k.Aggregate: _constant_aggregate,
    k.FnRef: lambda c: f"fn {c.symbol}",
    # the kinds of an aggregate, as the head of its text (ir.md 8.7)
    r.AggStruct: lambda kind: _type(kind.ty),
    r.AggTuple: lambda _kind: "",
    r.AggVariant: lambda kind: _variant(kind.ty, kind.variant, ()),
    r.AggArray: lambda _kind: "",
    r.AggClosure: lambda _kind: "closure",
    r.AggIterator: lambda _kind: "iterator",
    # rvalues (ir.md 8.7)
    r.Use: lambda n: show(n.operand),
    r.Binary: lambda n: f"{show(n.left)} {n.op} {show(n.right)}",
    r.Unary: lambda n: f"{n.op} {show(n.operand)}",
    r.Cast: lambda n: f"{show(n.operand)} as {_type(n.target)}",
    r.Aggregate: _aggregate,
    r.Discriminant: lambda n: f"discriminant({show(n.place)})",
    r.Clone: lambda n: f"clone({show(n.operand)})",
    r.Len: lambda n: f"len({show(n.place)})",
    r.Slice: lambda n: f"slice({show(n.place)}, {show(n.start)}, {show(n.end)})",
    r.Ref: lambda n: f"ref {_MODES[n.mode]} {show(n.place)}",
    # callees (ir.md 8.6)
    st.Direct: lambda c: f"@{c.name}" if c.module is None else f"@<{c.module}>.{c.name}",
    st.Builtin: _builtin,
    st.Indirect: lambda c: f"indirect({show(c.operand)})",
    st.BuiltinMethod: lambda m: f"{m.family}.{m.name}",
    st.Intrinsic: lambda m: m.name,
    # statements (ir.md 8.6)
    st.Assign: lambda n: f"{show(n.place)} = {show(n.rvalue)}",
    st.Call: lambda n: f"{show(n.dest)} = call {show(n.callee)}({', '.join(_arg(a) for a in n.args)})",
    st.Drop: lambda n: f"Drop({show(n.place)})",
    st.DropIfSet: lambda n: f"DropIfSet({show(n.place)}, {_local(n.flag)})",
    st.BeginBorrow: lambda n: f"BeginBorrow({_borrow(n.id)}, {show(n.place)}, {_MODES[n.mode]}"
                              f"{'' if n.view is None else f', for {_local(n.view)}'})",
    st.EndBorrow: lambda n: f"EndBorrow({_borrow(n.id)})",
    st.StorageLive: lambda n: f"StorageLive({_local(n.local)})",
    st.StorageDead: lambda n: f"StorageDead({_local(n.local)})",
    # terminators (ir.md 8.8)
    tm.Goto: lambda n: f"goto -> {_block(n.target)}",
    tm.SwitchInt: _switch,
    tm.Return: lambda _n: "return",
    tm.Trap: _trap,
    tm.Unreachable: lambda _n: "unreachable",
}


def _local_line(local: SlirLocal) -> str:
    text = f"    let {_local(local.id)}: {_type(local.ty)}{' view' if local.view else ''}"
    if local.name is None:
        return text
    return f"{text}{' ' * max(NAME_COLUMN - len(text), 2)}// {local.name}"


def print_function(function: SlirFunction) -> str:
    """The text of one function: its header, its locals and its blocks, in id order."""
    by_id = {local.id: local for local in function.locals}
    slot = by_id.get(LocalId(0))
    if slot is None:
        er.raise_internal_error("CE0000",
                                detail=f"the SLIR function '{function.name}' has no return slot")
    params = [ident for ident, _ in function.params]
    header = ", ".join(f"{_local(ident)}: {_type(by_id[ident].ty)} @{CONVENTIONS[convention]}"
                       for ident, convention in function.params)
    lines = [f"fn {function.name}({header}) -> {_type(slot.ty)} {{"]
    lines.extend(_local_line(local) for local in sorted(function.locals, key=lambda local: local.id)
                 if local.id not in params)
    for block in sorted(function.blocks, key=lambda block: block.id):
        lines.extend(["", f"  {_block(block.id)}:"])
        lines.extend(f"    {show(stmt)}" for stmt in block.statements)
        lines.append(f"    {show(block.terminator)}")
    lines.append("}")
    return "\n".join(lines) + "\n" + "".join("\n" + print_function(each)
                                             for each in function.lifted)


def print_stopped(stopped: SlirStopped) -> str:
    """The one line of a body whose SLIR lowering stopped."""
    return f"fn {stopped.name}: stopped ({STAGE}, {stopped.construct})\n"


def print_units(units: Sequence[tuple[str, Sequence[SlirOutcome]]]) -> str:
    """The text of a program: each unit, by its name, then the outcome of each body, in order."""
    parts: list[str] = []
    for name, outcomes in units:
        parts.append(f"unit {name}\n")
        parts.extend(print_function(outcome) if isinstance(outcome, SlirFunction)
                     else print_stopped(outcome) for outcome in outcomes)
    return "\n".join(parts)
