"""The calls of a `BuiltinMethod` callee: one row of `METHOD_FAMILIES` for each family of
`METHOD_TYPE_REGISTRY` that the emitter handles (docs/design/ir.md 8.6).

The receiver is the first operand. Each family keeps its emitters and takes the values of
the operands (ruling Q7); the array, the List, the HashMap and the Own family take the
address of their receiver. Every operand is evaluated before the family emits anything.
"""
from __future__ import annotations

from typing import Any, Callable

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.strings import emit_stdlib_string_call
from sushi_lang.backend.generics.hashmap import emit_hashmap_method
from sushi_lang.backend.generics.list import emit_list_method
from sushi_lang.backend.generics.maybe import (
    emit_builtin_maybe_method, emit_maybe_signature_method)
from sushi_lang.backend.generics.own import emit_own_method
from sushi_lang.backend.generics.results import (
    emit_builtin_result_method, emit_result_signature_method)
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    operand_address, operand_value, type_of_operand, type_of_place)
from sushi_lang.backend.types.arrays import emit_array_method
from sushi_lang.semantics.method_effects import (
    ARRAY_FAMILY, HASHMAP_FAMILY, LIST_FAMILY, MAYBE_FAMILY, OWN_FAMILY, PRIMITIVE_FAMILY,
    RESULT_FAMILY, STRING_FAMILY,
)
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.shir.callee import BuiltinMethod
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType, DynamicArrayType, EnumType, ReferenceType, StructType,
)


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


#: The emitter of a container family: the codegen, the method name, the address of the
#: receiver, the receiver type and the value of each argument.
_ContainerEmitter = Callable[[Any, str, ir.Value, StructType, list[ir.Value]], ir.Value]


_MethodRow = Callable[[FunctionState, BuiltinMethod, st.Call], ir.Value]


def _container(base: str, emit: _ContainerEmitter) -> _MethodRow:
    """The row of a built-in container family (`List@(T)`, `HashMap@(K, V)`, `Own@(T)`):
    the ADDRESS of the receiver, an `Inout` receiver and a `Guaranteed` one alike, then
    the value of each argument. The family reads the state of the container when it runs,
    after the last argument (docs/design/ir.md 8.6)."""
    def row(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
        if not statement.args:
            state.fault(f"the call of '{method.name}' has no receiver")
        receiver, _ = statement.args[0]
        receiver_type = type_of_operand(state, receiver)
        if not is_instance_of(receiver_type, base) or not isinstance(receiver_type,
                                                                     StructType):
            state.fault(f"the receiver of '{method.name}' has the type {receiver_type}")
        address = operand_address(state, receiver)
        args = [operand_value(state, operand) for operand, _ in statement.args[1:]]
        return emit(state.codegen, method.name, address, receiver_type, args)
    return row


#: The emitter of a method of the `result` or the `maybe` family that reads its receiver:
#: the codegen, the method name and the value of the receiver.
_WrapperEmitter = Callable[[Any, str, ir.Value], ir.Value]

#: The emitter of a method of the `result` or the `maybe` family with a signature row
#: (`map_err`, `or_err`): the codegen, the method name, the value and the type of the
#: receiver, the value and the type of the argument, and the type of the answer.
_SignatureEmitter = Callable[[Any, str, ir.Value, EnumType, ir.Value, Any, EnumType],
                             ir.Value]


def _wrapper(emit: _WrapperEmitter, emit_signature: _SignatureEmitter) -> _MethodRow:
    """The row of the `result` or the `maybe` family. A method that reads its receiver
    takes no argument, its one operand is `Guaranteed` (ir.md 8.3), and it answers a
    `bool`. A method that takes its receiver (`nom self`: `map_err`, `or_err`) has an
    `Owned` receiver and one argument, and it answers the type of the destination."""
    def row(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
        operands = 2 if method.nom_self else 1
        if len(statement.args) != operands:
            state.fault(f"the call of '{method.name}' has {len(statement.args)} operands")
        receiver, convention = statement.args[0]
        wanted = Convention.OWNED if method.nom_self else Convention.GUARANTEED
        if convention is not wanted:
            state.fault(f"the receiver {receiver} of '{method.name}' is "
                        f"{convention.value}, not {wanted.value}")
        receiver_type = type_of_operand(state, receiver)
        if not isinstance(receiver_type, EnumType):
            state.fault(f"the receiver of '{method.name}' has the type {receiver_type}")
        if not method.nom_self:
            return emit(state.codegen, method.name, operand_value(state, receiver))
        argument = statement.args[1][0]
        answer_type = type_of_place(state, statement.dest)
        if not isinstance(answer_type, EnumType):
            state.fault(f"the answer of '{method.name}' has the type {answer_type}")
        values = [operand_value(state, operand) for operand, _ in statement.args]
        return emit_signature(state.codegen, method.name, values[0], receiver_type,
                              values[1], type_of_operand(state, argument), answer_type)
    return row


#: One row for each family that the emitter handles, by the name of the family.
METHOD_FAMILIES: dict[str, _MethodRow] = {
    ARRAY_FAMILY: _array,
    LIST_FAMILY: _container("List", emit_list_method),
    HASHMAP_FAMILY: _container("HashMap", emit_hashmap_method),
    OWN_FAMILY: _container("Own", emit_own_method),
    PRIMITIVE_FAMILY: _primitive,
    STRING_FAMILY: _string,
    RESULT_FAMILY: _wrapper(emit_builtin_result_method, emit_result_signature_method),
    MAYBE_FAMILY: _wrapper(emit_builtin_maybe_method, emit_maybe_signature_method),
}


def emit_method(state: FunctionState, method: BuiltinMethod,
                statement: st.Call) -> ir.Value:
    """The value of a method call. A family with no row stops (`emit, <family> method`)."""
    row = METHOD_FAMILIES.get(method.family)
    if row is None:
        state.stop(f"{method.family} method")
    return row(state, method, statement)
