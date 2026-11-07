"""The value of a closure from its `Aggregate` (docs/design/ir.md 8.7)."""
from __future__ import annotations

from llvmlite import ir

from sushi_lang.backend.runtime.closures import materialize_function_ref
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r


def emit_closure_value(state: FunctionState, rvalue: r.Aggregate) -> ir.Value:
    """A named function as a value: the first part is its `FnRef`, with no environment. A
    part that follows it is a capture, which stops (`emit, AggClosure`)."""
    head = rvalue.parts[0] if len(rvalue.parts) == 1 else None
    if not (isinstance(head, p.Const) and isinstance(head.constant, k.FnRef)):
        state.stop(rvalue.kind)
    target = state.codegen.module.globals.get(head.constant.symbol)
    if not isinstance(target, ir.Function):
        state.fault(f"the module declares no function '{head.constant.symbol}'")
    return materialize_function_ref(state.codegen, target)
