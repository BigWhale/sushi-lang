"""The value of a closure from its `Aggregate`, and the declaration of a function that SLIR
lowering cuts from a lambda (docs/design/ir.md 8.2, 8.7).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.runtime.closures import (
    emit_capturing_closure, emit_empty_closure, materialize_function_ref)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.slir.function import SlirFunction

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_closure_value(state: FunctionState, rvalue: r.Aggregate,
                       captures: list[ir.Value]) -> ir.Value:
    """The first part is the `FnRef` of the function, and `captures` holds the value of each
    part after it. A named function has no environment and takes its thunk. A lifted function
    has the environment first in its own signature."""
    head = rvalue.parts[0]
    if not (isinstance(head, p.Const) and isinstance(head.constant, k.FnRef)):
        state.fault("a closure value with no function")
    target = state.codegen.module.globals.get(head.constant.symbol)
    if not isinstance(target, ir.Function):
        state.fault(f"the module declares no function '{head.constant.symbol}'")
    env = rvalue.kind.env if isinstance(rvalue.kind, r.AggClosure) else None
    if env is None:
        return materialize_function_ref(state.codegen, target)
    if not captures:
        return emit_empty_closure(state.codegen, target)
    return emit_capturing_closure(state.codegen, target, env, captures)


def declare_lifted(codegen: LLVMCodegen, function: SlirFunction) -> None:
    """The internal declaration of a lifted function: its one reader is the closure value in
    the body around it, so each module that defines that body holds its own copy."""
    types = codegen.types
    params = []
    for local, convention in function.params:
        ll = types.ll_type(function.locals[local.index].ty)
        params.append(ll.as_pointer() if convention.by_address else ll)
    declared = ir.Function(codegen.module, ir.FunctionType(
        types.ll_type(function.locals[0].ty), params), name=function.symbol)
    declared.linkage = "internal"
