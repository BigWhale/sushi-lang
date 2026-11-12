"""The calls of a `BuiltinMethod` callee: one row of `METHOD_FAMILIES` for each family of
`METHOD_TYPE_REGISTRY` that the emitter handles (docs/design/ir.md 8.6).

The receiver is the first operand. Each family keeps its emitters and takes the values of
the operands (ruling Q7); the array, the List, the HashMap and the Own family take the
address of their receiver. Every operand is evaluated before the family emits anything.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.strings import (
    emit_stdlib_string_call, emit_string_append)
from sushi_lang.backend.generics.container_table import ContainerMethod, blank
from sushi_lang.backend.generics.hashmap import emit_hashmap_method
from sushi_lang.backend.generics.hashmap.cursor import emit_hashmap_cursor_next
from sushi_lang.backend.generics.list import LIST_EMITTERS, emit_list_method
from sushi_lang.backend.generics.maybe import (
    emit_builtin_maybe_method, emit_maybe_signature_method)
from sushi_lang.backend.generics.own import emit_own_method
from sushi_lang.backend.generics.results import (
    emit_builtin_result_method, emit_result_signature_method)
from sushi_lang.backend.slir.foreign import emit_foreign_ptr_method
from sushi_lang.backend.slir.iterators import emit_consumer
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    operand_address, operand_value, place_address, type_of_operand, type_of_place)
from sushi_lang.backend.types.arrays import emit_array_method
from sushi_lang.backend.types.arrays.runs import moves_by_address
from sushi_lang.semantics.method_effects import (
    ARRAY_FAMILY, CONCAT_IN_PLACE, CURSOR_NEXT, FOREIGN_PTR_FAMILY, HASHMAP_FAMILY,
    ITERATOR_FAMILY, LIST_FAMILY, MAYBE_FAMILY, OWN_FAMILY, PRIMITIVE_FAMILY, RESULT_FAMILY, STRING_FAMILY, steps_a_cursor,
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
    """A method of `string`, through the one emitter of the string family. The row
    `concat_in_place` is the concat peephole (`_concat_in_place`)."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    if method.name == CONCAT_IN_PLACE:
        return _concat_in_place(state, statement)
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return emit_stdlib_string_call(state.codegen, method.name, values[0], values[1:])


def _concat_in_place(state: FunctionState, statement: st.Call) -> ir.Value:
    """`s := s.concat(x)` (docs/design/ir.md 8.9): the value of the argument, then the
    append of the string family into the address of the `Inout` receiver. The call answers
    `~`."""
    if len(statement.args) != 2:
        state.fault(f"the call of '{CONCAT_IN_PLACE}' has {len(statement.args)} operands")
    (receiver, convention), (argument, _) = statement.args
    if convention is not Convention.INOUT:
        state.fault(f"the receiver of '{CONCAT_IN_PLACE}' is {convention.value}, not inout")
    piece = operand_value(state, argument)
    emit_string_append(state.codegen, operand_address(state, receiver), piece)
    return blank(state.codegen)


def _foreign_ptr(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
    """A method of a `ptr`: the value of the receiver and of each argument."""
    if not statement.args:
        state.fault(f"the call of '{method.name}' has no receiver")
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return emit_foreign_ptr_method(state, method.name, values[0], values[1:])


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


def _container(base: str, emit: _ContainerEmitter,
               table: Mapping[str, ContainerMethod] | None = None) -> _MethodRow:
    """The row of a built-in container family (`List@(T)`, `HashMap@(K, V)`, `Own@(T)`):
    the ADDRESS of the receiver, an `Inout` receiver and a `Guaranteed` one alike, then
    the value of each argument. A method whose row of `table` is `by_address` takes the
    address of an argument that moves by address. The family reads the state of the
    container when it runs, after the last argument (docs/design/ir.md 8.6)."""
    def row(state: FunctionState, method: BuiltinMethod, statement: st.Call) -> ir.Value:
        if not statement.args:
            state.fault(f"the call of '{method.name}' has no receiver")
        receiver, _ = statement.args[0]
        receiver_type = type_of_operand(state, receiver)
        if not is_instance_of(receiver_type, base) or not isinstance(receiver_type,
                                                                     StructType):
            state.fault(f"the receiver of '{method.name}' has the type {receiver_type}")
        address = operand_address(state, receiver)
        row_of = table.get(method.name) if table is not None else None
        by_address = row_of is not None and row_of.by_address
        args = [operand_address(state, operand)
                if by_address and moves_by_address(
                    state.codegen.types.ll_type(type_of_operand(state, operand)))
                else operand_value(state, operand) for operand, _ in statement.args[1:]]
        return emit(state.codegen, method.name, address, receiver_type, args)
    return row


_hashmap_method = _container("HashMap", emit_hashmap_method)


def _hashmap(state: FunctionState, method: BuiltinMethod,
             statement: st.Call) -> ir.Value | None:
    """A method of a `HashMap@(K, V)`. A `cursor_next` row writes the item of its view into
    the destination, in the form of its type, and answers None (docs/design/ir.md 8.6)."""
    if not steps_a_cursor(method):
        return _hashmap_method(state, method, statement)
    if len(statement.args) != 2:
        state.fault(f"the call of '{method.name}' has {len(statement.args)} operands")
    (owner, _), (cursor, _) = statement.args
    map_type, answer = type_of_operand(state, owner), type_of_place(state, statement.dest)
    if not isinstance(map_type, StructType) or not isinstance(answer, EnumType):
        state.fault(f"the call of '{method.name}' has the types {map_type} and {answer}")
    emit_hashmap_cursor_next(state.codegen, method.name.removeprefix(CURSOR_NEXT),
                             operand_address(state, owner), operand_address(state, cursor),
                             map_type, answer, place_address(state, statement.dest))
    return None


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
    LIST_FAMILY: _container("List", emit_list_method, LIST_EMITTERS),
    HASHMAP_FAMILY: _hashmap,
    OWN_FAMILY: _container("Own", emit_own_method),
    PRIMITIVE_FAMILY: _primitive,
    STRING_FAMILY: _string,
    RESULT_FAMILY: _wrapper(emit_builtin_result_method, emit_result_signature_method),
    MAYBE_FAMILY: _wrapper(emit_builtin_maybe_method, emit_maybe_signature_method),
    ITERATOR_FAMILY: emit_consumer,
    FOREIGN_PTR_FAMILY: _foreign_ptr,
}


def emit_method(state: FunctionState, method: BuiltinMethod,
                statement: st.Call) -> ir.Value | None:
    """The value of a method call, or None when the row wrote the destination itself. A
    family with no row is an internal error."""
    row = METHOD_FAMILIES.get(method.family)
    if row is None:
        state.unhandled(f"{method.family} method")
    return row(state, method, statement)
