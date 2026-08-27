"""The IR twin of `semantics/ranges.py`, for bounds the compiler cannot read (#478).

Same formula, five instructions:

    count = |end - start| + (inclusive ? 1 : 0)
    step  = (end >= start) ? +1 : -1

`semantics/ranges.py` states it over Python integers and this module states it as IR. Both
live behind a named seam, and `tests/unit/test_range_plan_matrix.py` pins them against each
other so neither can drift.

llvmlite does not fold, so a readable range must NEVER reach here: `builder.add` of two
constants emits `add i32 3, 4` into the module, and at `--opt none` there is no second
chance. `runs.py` picks the tier and calls this only for a bound it could not read.
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
    """A range as three i32 values: where it starts, which way it walks, and how far."""
    first: ir.Value
    step: ir.Value
    count: ir.Value


def emit_range(codegen: 'LLVMCodegen', expr: 'RangeExpr') -> EmittedRange:
    """Emit the range formula for bounds that are only known at run time."""
    b = codegen.builder
    i32 = codegen.types.i32

    first = codegen.utils.as_i32(codegen.expressions.emit_expr(expr.start))
    end = codegen.utils.as_i32(codegen.expressions.emit_expr(expr.end))

    span = b.sub(end, first, name="range_span")
    descending = b.icmp_signed("<", span, ir.Constant(i32, 0), name="range_descending")
    magnitude = b.select(descending, b.sub(ir.Constant(i32, 0), span), span,
                         name="range_magnitude")

    count = magnitude
    if expr.inclusive:
        count = b.add(magnitude, ir.Constant(i32, 1), name="range_count")

    step = b.select(descending, ir.Constant(i32, -1), ir.Constant(i32, 1), name="range_step")
    return EmittedRange(first=first, step=step, count=count)
