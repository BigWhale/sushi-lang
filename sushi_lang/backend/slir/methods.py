"""The calls of a `BuiltinMethod` callee: one row of `METHOD_FAMILIES` for each family of
`METHOD_TYPE_REGISTRY` that the emitter handles (docs/design/ir.md 8.6).

The receiver is the first operand. Each family keeps its emitters and takes the values of
the operands (ruling Q7). Every operand is evaluated before the family emits anything.
"""
from __future__ import annotations

from typing import Callable

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.strings import emit_stdlib_string_call
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import operand_value, type_of_operand
from sushi_lang.semantics.shir.callee import BuiltinMethod
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import ReferenceType


def _primitive(state: FunctionState, method: BuiltinMethod,
               statement: st.Call) -> ir.Value:
    """A method of a primitive type, through the emitter that the registry holds for the
    receiver type and the name."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    receiver, _ = statement.args[0]
    receiver_type = type_of_operand(state, receiver)
    if isinstance(receiver_type, ReferenceType):
        state.fault(f"the receiver of '{method.name}' has the reference type {receiver_type}")
    entry = state.codegen.derived_methods.get_method(receiver_type, method.name)
    if entry is None:
        state.fault(f"no primitive method '{method.name}' on {receiver_type}")
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return entry.llvm_emitter(state.codegen, values[1:], values[0], values[0].type)


def _string(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
    """A method of `string`, through the one emitter of the string family."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return emit_stdlib_string_call(state.codegen, method.name, values[0], values[1:])


#: One row for each family that the emitter handles, by the name of the family.
METHOD_FAMILIES: dict[str, Callable[[FunctionState, BuiltinMethod, st.Call], ir.Value]] = {
    "primitive": _primitive,
    "string": _string,
}


def emit_method(state: FunctionState, method: BuiltinMethod,
                statement: st.Call) -> ir.Value:
    """The value of a method call. A family with no row stops (`emit, <family> method`)."""
    row = METHOD_FAMILIES.get(method.family)
    if row is None:
        state.stop(f"{method.family} method")
    return row(state, method, statement)
