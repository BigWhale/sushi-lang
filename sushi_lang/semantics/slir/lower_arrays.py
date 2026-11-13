"""SHIR to SLIR: the arms of an array (docs/design/ir.md 8.7, 8.9).

An `ArrayInit` is ONE `Aggregate(AggArray)` with a run for each run of the literal: the
values in the written order (ruling R1), the value of a repeat before its count (ruling R2).
A range is a sequence: two constant bounds give its first value and its count as constants
(`plan_range`), and a computed bound gives them as locals of the statement. An element is
the place `Index(place, i)`, and a slice is a `Slice` in a VIEW local, which owns nothing.
A value of an owning element: a `Single` value moves into its slot, and a repeated value is
a borrow that the emitter copies into each slot. A write of an owning element drops the old
element after the new value and the check of the index (`lower_aggregate.part_write`).
Each index and each bound goes into a local of the statement first, so a later call cannot
change it between the check and the access. A checked index compares the index and the
length as `u32` and goes to the trap RE2020 on the failing edge; a slice checks
`0 <= start <= end <= len` and goes to RE2028. An index under `dont_panic` has no check, and
neither has a constant index that typecheck proved in range. The locals of a compare get no
storage marker. Each arm is a free function that takes the `Lowering` of `lower.py` first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.operators import BinOp
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir import terminators as tm
from sushi_lang.semantics.slir.lower_control import bound, i32, int_of, range_operands
from sushi_lang.semantics.slir.lower_values import Value
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, Type, deref_type,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering

I32, U32, BOOL = BuiltinType.I32, BuiltinType.U32, BuiltinType.BOOL


def array_init(low: Lowering, node: e.ArrayInit) -> Value:
    """`Aggregate(AggArray)` of the type of the literal, its runs in order."""
    ty = low.type_of(node)
    if not isinstance(ty, (ArrayType, DynamicArrayType)):
        er.raise_internal_error("CE0000", span=node.loc,
                                detail=f"an array construction of {display_type(ty)} reached "
                                       f"SLIR")
    values: list[e.ShirExpr] = []
    for run in node.runs:
        if isinstance(run, e.Single):
            values.append(run.value)
        elif isinstance(run, e.Repeat):
            values += [run.value, run.count]
        else:
            values += [run.range.start, run.range.end]
    operands = iter(agg.in_written_order(low, values, range(len(values))))
    runs: list[r.ArrayRun] = []
    parts: list[p.Operand] = []
    for run in node.runs:
        if isinstance(run, e.Single):
            runs.append(r.RunOne())
            parts.append(next(operands))
        elif isinstance(run, e.Repeat):
            runs.append(r.RunRepeat())
            parts += [next(operands), next(operands)]
        else:
            runs.append(r.RunSequence(-1 if run.range.reverse else 1))
            parts += range_operands(low, run.range, next(operands), next(operands))
    return r.Aggregate(r.AggArray(ty, tuple(runs)), tuple(parts))


def element(low: Lowering, base: p.Place, node: e.Index) -> p.Place:
    """`Index(base, i)`: the index into a local of the statement, then the bounds check."""
    index = bound(low, node.index)
    if not node.unchecked:
        length = length_of(low, base, low.type_of(node.base), node.loc)
        at, size = int_of(index), int_of(length)
        if at is None or size is None or not 0 <= at < size:
            ok = _compare(low, "<", index, length, node.loc)
            guard(low, ok, tm.Trap("RE2020", (index, length), node.loc), node.loc)
    return p.Index(base, index)


def view(low: Lowering, base: p.Place, node: e.Slice) -> p.Place:
    """A view local that a `Slice` writes, after the check `0 <= start <= end <= len`. An
    inclusive end is one more, so the `Slice` and the trap read an exclusive end."""
    span = node.loc
    start = bound(low, node.start)
    end = _add(low, bound(low, node.end), node.inclusive, span)
    length = length_of(low, base, low.type_of(node.base), span)
    first, last, size = int_of(start), int_of(end), int_of(length)
    if first is None or last is None or size is None or not 0 <= first <= last <= size:
        end_ok = _compare(low, "<=", end, length, span)
        start_ok = _compare(low, "<=", start, end, span)
        ok = check_local(low, r.Binary("&", end_ok, start_ok), BOOL, span)
        guard(low, ok, tm.Trap("RE2028", (start, end, length), span), span)
    ident = low.new_local(low.type_of(node), None, None)
    low.placement.temporary(ident, False)
    low.emit([st.StorageLive(ident, span), st.Assign(p.Local(ident),
                                                      r.Slice(base, start, end), span)])
    return p.Local(ident)


def length_of(low: Lowering, base: p.Place, ty: Type, span: Span) -> p.Operand:
    """The length of a `T[N]` is a constant; of a `T[]`, a `List@(T)` and a `string`, a `Len`
    into a local of the statement, which the trap reads too."""
    held = deref_type(ty)
    if isinstance(held, ArrayType):
        return i32(held.size)
    return p.Copy(p.Local(low.temporary(r.Len(base), I32, span)))


def _compare(low: Lowering, op: BinOp, left: p.Operand, right: p.Operand,
             span: Span) -> p.Operand:
    """`left op right` over the two `i32` values read as `u32`: a negative value is above
    every length."""
    return check_local(low, r.Binary(op, _unsigned(low, left, span),
                                      _unsigned(low, right, span)), BOOL, span)


def _unsigned(low: Lowering, operand: p.Operand, span: Span) -> p.Operand:
    value = int_of(operand)
    if value is not None:
        return p.Const(k.Int(value % (1 << 32), U32))
    return check_local(low, r.Cast(operand, U32), U32, span)


def check_local(low: Lowering, value: r.Rvalue, ty: Type, span: Span) -> p.Operand:
    """A local of a compare: its own block writes it and reads it, so it gets no storage
    marker (ir.md 8.9)."""
    ident = low.new_local(ty, None, None)
    low.emit([st.Assign(p.Local(ident), value, span)])
    return p.Copy(p.Local(ident))


def guard(low: Lowering, ok: p.Operand, trap: tm.Trap, span: Span) -> None:
    """`switchInt(ok, [(0, fail)], otherwise: next)`; the failing block ends in `trap`,
    which drops nothing. The blocks are made in the order fail, next."""
    fail, next_ = low.new_block(), low.new_block()
    low.jump(tm.SwitchInt(ok, ((0, fail.id),), next_.id, span))
    fail.terminator = trap
    low.block = next_


def _add(low: Lowering, operand: p.Operand, one: bool, span: Span) -> p.Operand:
    """`operand`, plus one when `one`: a constant stays a constant."""
    value = int_of(operand)
    if not one:
        return operand
    if value is not None:
        return i32(value + 1)
    return p.Copy(p.Local(low.temporary(r.Binary("+", operand, i32(1)), I32, span)))
