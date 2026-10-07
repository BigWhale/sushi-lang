"""The IR twin of `semantics/ranges.py`, for bounds the compiler cannot read (#478).

Same formula. A range always goes up, and `.rev()` walks the same values, last first:

    count = max(end - start + (inclusive ? 1 : 0), 0)
    step  = reverse ? -1 : +1
    first = reverse ? start + count - 1 : start

The clamp is a `select`, so a range whose end is below its start has a count of zero and
the walk does not start. `semantics/ranges.py` states the formula over Python integers and
this module states it as IR. `foreach` over a range and an array-literal range element both
call `emit_range`, so the two positions share one formula.

llvmlite does not fold, so a readable range must NEVER reach here from an array literal:
`builder.add` of two constants emits `add i32 3, 4` into the module, and at `--opt none`
there is no second chance. `runs.py` picks the tier and calls this only for a bound it
could not read.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import RangeExpr


@dataclass(frozen=True)
class EmittedRange:
    """A range as three i32 values: where the walk starts, which way it steps, how far."""
    first: ir.Value
    step: ir.Value
    count: ir.Value


def emit_range(codegen: 'LLVMCodegen', expr: 'RangeExpr') -> EmittedRange:
    """Emit the range formula for bounds that are only known at run time."""
    b = codegen.builder
    i32 = codegen.types.i32

    start = codegen.utils.require_i32(codegen.expressions.emit_expr(expr.start))
    end = codegen.utils.require_i32(codegen.expressions.emit_expr(expr.end))

    span = b.sub(end, start, name="range_span")
    if expr.inclusive:
        span = b.add(span, ir.Constant(i32, 1), name="range_span_inclusive")
    empty = b.icmp_signed("<", span, ir.Constant(i32, 0), name="range_empty")
    count = b.select(empty, ir.Constant(i32, 0), span, name="range_count")

    if not expr.reverse:
        return EmittedRange(first=start, step=ir.Constant(i32, 1), count=count)
    last = b.sub(b.add(start, count, name="range_past"), ir.Constant(i32, 1),
                 name="range_last")
    return EmittedRange(first=last, step=ir.Constant(i32, -1), count=count)
