"""The calls of an `Intrinsic` callee: `interp`, `print`, `println`, `eq`, the statics of
a primitive type name and the statics of a built-in container (docs/design/ir.md 8.6).

Each row gives the LLVM values of every operand to an emitter family of the backend (ruling
Q7), and the family reads the type of each operand from the callee. A static of a container
reads the instance that it builds from the type of its destination. Every operand is
evaluated before the family emits anything (ir.md 11.9). An operand is only read, except
the array of `string.from_bytes`, which the string takes, and the value of `Own.alloc`,
which the allocation takes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from llvmlite import ir

from sushi_lang.backend.generics.hashmap.methods.core import emit_hashmap_new
from sushi_lang.backend.generics.list import emit_list_static
from sushi_lang.backend.generics.own import emit_own_alloc
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import constant_value, operand_value, type_of_place
from sushi_lang.backend.types.arrays.addressing import as_array_address
from sushi_lang.backend.types.arrays.indexing import array_data, array_length
from sushi_lang.backend.types.contracts import emit_value_eq
from sushi_lang.backend.types.display import emit_interpolation
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.generics.own import get_own_element_type
from sushi_lang.semantics.typesys import BuiltinType, StructType, Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def operands(state: FunctionState, statement: st.Call) -> list[tuple[ir.Value, Type]]:
    """The value of each argument of an intrinsic call, with the type that the callee gives
    it, in order."""
    callee = statement.callee
    if not isinstance(callee, st.Builtin) or len(callee.types) != len(statement.args):
        state.fault(f"an intrinsic call with {len(statement.args)} arguments and the "
                    f"callee {callee}")
    values = [operand_value(state, operand) for operand, _ in statement.args]
    return list(zip(values, callee.types, strict=True))


def _interp(state: FunctionState, statement: st.Call) -> ir.Value:
    """A fresh owned string, from the string builder."""
    return emit_interpolation(state.codegen, operands(state, statement))


def _print(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=False)


def _println(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=True)


def _write(state: FunctionState, statement: st.Call, *, is_line: bool) -> ir.Value:
    """One value to the console, through the print emitter. A value that prints by its
    `Display` form stops for now. The call answers `~`."""
    parts = operands(state, statement)
    if len(parts) != 1:
        state.fault(f"a print of {len(parts)} values")
    (value, ty), = parts
    if ty != BuiltinType.STRING and ty != BuiltinType.BOOL and not is_numeric_type(ty):
        state.stop("Display print")
    state.codegen.runtime.formatting.emit_print_value(value, is_line=is_line,
                                                      semantic_type=ty)
    return constant_value(state, k.Blank())


def _eq(state: FunctionState, statement: st.Call) -> ir.Value:
    """The equality of two values of one type, through the contract seam. A match on a
    string tests each literal arm with it. The answer is a `bool`."""
    parts = operands(state, statement)
    if len(parts) != 2 or parts[0][1] != parts[1][1]:
        state.fault(f"an eq of {len(parts)} values of the types {[ty for _, ty in parts]}")
    (left, ty), (right, _) = parts
    answer = emit_value_eq(state.codegen, left, right, ty)
    return state.builder.zext(answer, state.codegen.types.ll_type(BuiltinType.BOOL))


def _from_bits(float_type: ir.Type) -> Callable[[FunctionState, st.Call], ir.Value]:
    """`f64.from_bits(bits)` and `f32.from_bits(bits)`: the bits of the integer, read as a
    float of the same width."""
    def row(state: FunctionState, statement: st.Call) -> ir.Value:
        parts = operands(state, statement)
        if len(parts) != 1:
            state.fault(f"a from_bits of {len(parts)} values")
        return state.builder.bitcast(parts[0][0], float_type, name="from_bits")
    return row


def _string_from_bytes(state: FunctionState, statement: st.Call) -> ir.Value:
    """`string.from_bytes(nom b)` (#1091): the buffer of the array becomes the buffer of
    the string, with no copy. The string owns it (`owned` is 1)."""
    parts = operands(state, statement)
    if len(parts) != 1:
        state.fault(f"a from_bytes of {len(parts)} values")
    (value, ty), = parts
    codegen = state.codegen
    address = as_array_address(codegen, value)
    string = ir.Constant(codegen.types.string_struct, ir.Undefined)
    string = state.builder.insert_value(string, array_data(codegen, address, ty), 0)
    string = state.builder.insert_value(string, array_length(codegen, address, ty), 1)
    return state.builder.insert_value(string, ir.Constant(codegen.i8, 1), 2,
                                      name="from_bytes")


#: The emitter of a container static: the codegen, the value of each operand, and the
#: instance that the destination holds.
_StaticEmitter = Callable[["LLVMCodegen", list[ir.Value], StructType], ir.Value]


def _container_static(emit: _StaticEmitter,
                      count: int) -> Callable[[FunctionState, st.Call], ir.Value]:
    """A static of a built-in container with `count` operands: its emitter takes the value
    of each operand and the instance that the destination holds."""
    def row(state: FunctionState, statement: st.Call) -> ir.Value:
        parts = operands(state, statement)
        if len(parts) != count:
            state.fault(f"a static of a container with {len(parts)} values, not {count}")
        instance = type_of_place(state, statement.dest)
        if not isinstance(instance, StructType):
            state.fault(f"a static of a container builds the type {instance}")
        return emit(state.codegen, [value for value, _ in parts], instance)
    return row


def _list_static(name: str) -> _StaticEmitter:
    """A static of `List@(T)`, through its row of the List table."""
    return lambda codegen, values, list_type: emit_list_static(codegen, name, list_type,
                                                               values)


def _hashmap_new(codegen: LLVMCodegen, _values: list[ir.Value], map_type: StructType) -> ir.Value:
    return emit_hashmap_new(codegen, map_type)


def _own_alloc(codegen: LLVMCodegen, values: list[ir.Value], own_type: StructType) -> ir.Value:
    return emit_own_alloc(codegen, get_own_element_type(own_type), values[0])


#: One row for each intrinsic that the emitter handles, by the name of the intrinsic.
INTRINSICS: dict[str, Callable[[FunctionState, st.Call], ir.Value]] = {
    "f64.from_bits": _from_bits(ir.DoubleType()),
    "f32.from_bits": _from_bits(ir.FloatType()),
    "string.from_bytes": _string_from_bytes,
    "List.new": _container_static(_list_static("new"), 0),
    "List.with_capacity": _container_static(_list_static("with_capacity"), 1),
    "HashMap.new": _container_static(_hashmap_new, 0),
    "Own.alloc": _container_static(_own_alloc, 1),
    "eq": _eq,
    "interp": _interp,
    "print": _print,
    "println": _println,
}
