"""The checks of an array value, a length and a slice in the SLIR verifier (docs/design/ir.md 8.13).

An `Aggregate(AggArray)` gives the parts of each run: a value of the element type, an
`i32` count, and an `i32` first value of a sequence of `i32` slots. The counts of a `T[N]`
are constants that add up to `N`. `Len` reads an array or a string, and `Slice` reads an
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
from sushi_lang.semantics.slir.printer import show
from sushi_lang.semantics.slir.verify_rules import ARRAY_VALUE, LENGTH_SLICE
from sushi_lang.semantics.typesys import ArrayType, BuiltinType, DynamicArrayType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.verify import Check


def array_value(check: Check, kind: r.AggArray, parts: Sequence[p.Operand], span: Span) -> None:
    """The parts fit the runs: a value of the element type, an `i32` count, an `i32`
    first value of a sequence of `i32` slots. The counts of a `T[N]` are constants
    that add up to `N`."""
    ty = kind.ty
    if not isinstance(ty, (ArrayType, DynamicArrayType)):
        check.fail(ARRAY_VALUE, f"an array value of {display_type(ty)}, which is no array",
                   span)
    widths = [r.run_width(run) for run in kind.runs]
    if sum(widths) != len(parts):
        check.fail(ARRAY_VALUE, f"the runs of {display_type(ty)} take {sum(widths)} parts, "
                                f"and the value gives {len(parts)}", span)
    wanted: list[Type] = []
    for run in kind.runs:
        if isinstance(run, r.RunSequence) and ty.base_type != BuiltinType.I32:
            check.fail(ARRAY_VALUE, f"a sequence fills {display_type(ty)}, whose element "
                                    f"is no i32", span)
        first = BuiltinType.I32 if isinstance(run, r.RunSequence) else ty.base_type
        wanted += [first] if isinstance(run, r.RunOne) else [first, BuiltinType.I32]
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
    """`Len` reads an array or a string; `Slice` reads an array with two `i32` bounds."""
    ty = check.place(value.place, span).ty
    kinds: tuple[type, ...] = (ArrayType, DynamicArrayType)
    if isinstance(value, r.Len) and ty == BuiltinType.STRING:
        return
    if ty is not None and not isinstance(ty, kinds):
        check.fail(LENGTH_SLICE, f"{show(value)} reads a value that is no array", span)
    if isinstance(value, r.Slice):
        for bound in (value.start, value.end):
            if check.operand_type(bound, span) not in (BuiltinType.I32, None):
                check.fail(LENGTH_SLICE, f"the bound {show(bound)} of {show(value)} is no "
                                         f"i32", span)
