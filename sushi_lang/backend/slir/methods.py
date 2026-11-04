"""The calls of a `BuiltinMethod` callee: one row of `METHOD_FAMILIES` for each family of
`METHOD_TYPE_REGISTRY` that the emitter handles (docs/design/ir.md 8.6).

The receiver is the first operand. Each family keeps its emitters and takes the values of
the operands (ruling Q7); the array family takes the address of its receiver. Every operand
is evaluated before the family emits anything.
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.strings import emit_stdlib_string_call
from sushi_lang.backend.generics.maybe import emit_builtin_maybe_method
from sushi_lang.backend.generics.results import emit_builtin_result_method
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import operand_address, operand_value, type_of_operand
from sushi_lang.backend.types.arrays import emit_array_method
from sushi_lang.semantics.method_effects import (
    ARRAY_FAMILY, MAYBE_FAMILY, PRIMITIVE_FAMILY, RESULT_FAMILY, STRING_FAMILY,
)
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, EnumType, ReferenceType


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


def _array(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
    """A method of a `T[N]` or a `T[]`: the ADDRESS of the receiver, an `Inout` receiver
    and a `Guaranteed` one alike, then the value and the type of each argument. The family
    reads the length and the data of the array when it runs, after the last argument
    (docs/design/ir.md 8.6)."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    receiver, _ = statement.args[0]
    receiver_type = type_of_operand(state, receiver)
    if not isinstance(receiver_type, (ArrayType, DynamicArrayType)):
        state.fault(f"the receiver of '{method.name}' has the type {receiver_type}")
    address = operand_address(state, receiver)
    args = [(operand_value(state, operand), type_of_operand(state, operand))
            for operand, _ in statement.args[1:]]
    return emit_array_method(state.codegen, method.name, address, receiver_type, args)


_MethodRow = Callable[[FunctionState, BuiltinMethod, st.Call], ir.Value]


#: The emitter of the `result` or the `maybe` family: the codegen, the method name, the
#: value and the type of the receiver, and the values of the arguments.
_WrapperEmitter = Callable[[Any, str, ir.Value, EnumType, Sequence[ir.Value]], ir.Value]


def _wrapper(emit: _WrapperEmitter) -> _MethodRow:
    """The row of the `result` or the `maybe` family, over its emitter `emit`. Every
    operand is `Guaranteed` (ir.md 8.3), so the family copies an owning value that it
    answers."""
    def row(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
        if not statement.args:
            state.fault(f"the call of '{method.name}' has no receiver")
        for operand, convention in statement.args:
            if convention is not Convention.GUARANTEED:
                state.fault(f"the operand {operand} of '{method.name}' is "
                            f"{convention.value}, not guaranteed")
        receiver_type = type_of_operand(state, statement.args[0][0])
        if not isinstance(receiver_type, EnumType):
            state.fault(f"the receiver of '{method.name}' has the type {receiver_type}")
        values = [operand_value(state, operand) for operand, _ in statement.args]
        return emit(state.codegen, method.name, values[0], receiver_type, values[1:])
    return row


#: One row for each family that the emitter handles, by the name of the family.
METHOD_FAMILIES: dict[str, _MethodRow] = {
    ARRAY_FAMILY: _array,
    PRIMITIVE_FAMILY: _primitive,
    STRING_FAMILY: _string,
    RESULT_FAMILY: _wrapper(emit_builtin_result_method),
    MAYBE_FAMILY: _wrapper(emit_builtin_maybe_method),
}


def emit_method(state: FunctionState, method: BuiltinMethod,
                statement: st.Call) -> ir.Value:
    """The value of a method call. A family with no row stops (`emit, <family> method`)."""
    row = METHOD_FAMILIES.get(method.family)
    if row is None:
        state.stop(f"{method.family} method")
    return row(state, method, statement)
