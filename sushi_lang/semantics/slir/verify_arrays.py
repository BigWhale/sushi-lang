"""The checks of an array value, a length, a slice and the drop of a part in the SLIR verifier (docs/design/ir.md 8.13).

An `Aggregate(AggArray)` gives the parts of each run: a value of the element type, an
`i64` count, and an `i32` first value of a sequence of `i32` slots. The value of a repeat
run is a borrow, never a `Move`. The counts of a `T[N]` are constants that add up to `N`. A
`Drop` of a field, an element or a payload is of an owning type, off a local that is no view. `Len` reads an array, a `List@(T)` or a string, and `Slice` reads an
array with two `i32` bounds. Each check is a free function that takes the `Check` of
`verify.py` first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.slir.verify_rules import ARRAY_VALUE, LENGTH_SLICE, OWNING_DROP, VIEW
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, Type, owns_resource,
)

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check


def array_value(check: Check, kind: r.AggArray, parts: Sequence[p.Operand], span: Span) -> None:
    """The parts fit the runs: a value of the element type and an `i64` count of a repeat,
    an `i32` first value and an `i64` count of a sequence of `i32` slots. The value of a repeat run is a borrow, never
    a `Move`. The counts of a `T[N]` are constants that add up to `N`."""
    ty = kind.ty
    if not isinstance(ty, (ArrayType, DynamicArrayType)):
        check.fail(ARRAY_VALUE, f"an array value of {display_type(ty)}, which is no array",
                   span)
    widths = [r.run_width(run) for run in kind.runs]
    if sum(widths) != len(parts):
        check.fail(ARRAY_VALUE, f"the runs of {display_type(ty)} take {sum(widths)} parts, "
                                f"and the value gives {len(parts)}", span)
    wanted: list[Type] = []
    at = 0
    for run in kind.runs:
        if isinstance(run, r.RunRepeat) and isinstance(parts[at], p.Move):
            check.fail(ARRAY_VALUE, f"a repeat run of {display_type(ty)} moves its value "
                                    f"{show(parts[at])}, which it borrows", span)
        at += r.run_width(run)
        if isinstance(run, r.RunSequence) and ty.base_type != BuiltinType.I32:
            check.fail(ARRAY_VALUE, f"a sequence fills {display_type(ty)}, whose element "
                                    f"is no i32", span)
        first = BuiltinType.I32 if isinstance(run, r.RunSequence) else ty.base_type
        wanted += [first] if isinstance(run, r.RunOne) else [first, BuiltinType.I64]
    check.parts_fit(ARRAY_VALUE, display_type(ty), wanted,
                    [check.operand_type(part, span) for part in parts], span)
    if isinstance(ty, ArrayType):
        _fixed_size(check, ty, kind.runs, parts, span)


def _fixed_size(check: Check, ty: ArrayType, runs: Sequence[r.ArrayRun],
                parts: Sequence[p.Operand], span: Span) -> None:
    total, at = 0, 0
    for run in runs:
        if isinstance(run, r.RunOne):
            total, at = total + 1, at + 1
            continue
        count = parts[at + 1]
        if not (isinstance(count, p.Const) and isinstance(count.constant, k.Int)):
            check.fail(ARRAY_VALUE, f"the count {show(count)} of a run of "
                                    f"{display_type(ty)} is no constant", span)
        total, at = total + max(count.constant.value, 0), at + 2
    if total != ty.size:
        check.fail(ARRAY_VALUE, f"the runs of {display_type(ty)} fill {total} slots", span)


def length_or_slice(check: Check, value: r.Len | r.Slice, span: Span) -> None:
    """`Len` reads an array, a `List@(T)` or a string; `Slice` reads an array with two `i64`
    bounds."""
    ty = check.place(value.place, span).ty
    kinds: tuple[type, ...] = (ArrayType, DynamicArrayType)
    if isinstance(value, r.Len) and (ty == BuiltinType.STRING or is_instance_of(ty, "List")):
        return
    if ty is not None and not isinstance(ty, kinds):
        check.fail(LENGTH_SLICE, f"{show(value)} reads a value that is no array", span)
    if isinstance(value, r.Slice):
        for bound in (value.start, value.end):
            if check.operand_type(bound, span) not in (BuiltinType.I64, None):
                check.fail(LENGTH_SLICE, f"the bound {show(bound)} of {show(value)} is no "
                                         f"i64", span)


def part_drop(check: Check, stmt: st.Drop) -> None:
    """The `Drop` of a field, an element or a payload: the old value at the write of an
    owning part (ir.md 8.10, rule 6). Its root is a local that is no view, and its type owns;
    a payload owns when one of its values owns. The dataflow reads the root, which must hold
    a value, and does not change its state."""
    typed = check.place(stmt.place, stmt.span)
    if isinstance(typed.root, p.Local) and typed.root.id in check.views:
        check.fail(VIEW, f"{show(stmt.place)} is a part of a view, which owns nothing to drop",
                   stmt.span)
    held = check.held_types(typed.ty)
    if not any(owns_resource(ty, check.drops) for ty in held):
        shown = " and ".join(display_type(ty) for ty in held) or "no value"
        check.fail(OWNING_DROP, f"{show(stmt.place)} has the type {shown}, which owns nothing",
                   stmt.span)
