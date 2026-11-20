"""SHIR to SLIR: the variadic tail of a call (docs/design/ir.md 7.4, 8.9).

The tail row of the callee (`ResolvedCall.tail`) says where the tail starts. The values of
the tail go into ONE temporary `T[]` local of the call, as ONE `Aggregate(AggArray)` in the
written order: a value that owns is the `Move` of its temporary. An empty tail is the
empty array. The temporary is the operand of the tail, with the convention of the tail
parameter: a `Guaranteed` tail is a borrow of the callee, and the temporary drops at the end
of the statement. A bloom `arr...` lends its source: the operand is the place of the source,
with no temporary, and the caller keeps the array. Each arm is a free function that takes
the `Lowering` of `lower.py` first.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.typeck import Tail
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir.lower_values import Value

if TYPE_CHECKING:
    from sushi_lang.semantics.slir.lower import Lowering


def tail_argument(low: Lowering, node: e.Call, tail: Tail) -> tuple[p.Operand, Convention]:
    """The one operand of the tail of `node`, with the convention of the tail parameter."""
    values = node.args[tail.position:]
    if len(values) == 1 and values[0].bloom:
        return low.operand(values[0].value), tail.convention
    exprs = [arg.value for arg in values]
    operands = agg.in_written_order(low, exprs, range(len(exprs)))
    array: Value = r.Aggregate(r.AggArray(tail.ty, (r.RunOne(),) * len(operands)), operands)
    local = p.Local(low.temporary(array, tail.ty, node.loc))
    return (p.Move(local) if tail.convention.consumes else p.Copy(local)), tail.convention
