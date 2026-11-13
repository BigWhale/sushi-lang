"""The calls of an `Intrinsic` callee: `interp`, `print`, `println`, `errno`, the contract calls
`hash`, `eq`, `compare` and `to_str`, the statics of a primitive type name and the statics
of a built-in container (docs/design/ir.md 8.6).

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
from sushi_lang.backend.string_layout import string_value

from sushi_lang.backend.destructors import emit_value_destructor
from sushi_lang.backend.generics.hashmap import emit_hashmap_static
from sushi_lang.backend.generics.list import emit_list_static
from sushi_lang.backend.generics.own import emit_own_static
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    by_address_or_value, constant_value, operand_value, type_of_place)
from sushi_lang.backend.types.arrays.addressing import as_array_address
from sushi_lang.backend.types.arrays.indexing import array_data, array_length
from sushi_lang.backend.types.contracts import emit_value_compare, emit_value_eq
from sushi_lang.backend.types.display import emit_interpolation, emit_value_to_str
from sushi_lang.backend.types.value_hash import emit_value_hash
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, StructType, Type
from sushi_lang.sushi_stdlib.src.libc_declarations import declare_errno_location

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


#: The types that the print emitter writes with no `Display` form, beside a number. A `~`
#: answer prints as an integer zero.
_PRINTS_AS_IT_IS = frozenset({BuiltinType.STRING, BuiltinType.BOOL, BuiltinType.BLANK})


def operands(state: FunctionState, statement: st.Call,
             by_address: bool = False) -> list[tuple[ir.Value, Type]]:
    """The value of each argument of an intrinsic call, with the type that the callee gives
    it, in order. With `by_address`, an argument that moves by address is its address."""
    callee = statement.callee
    if not isinstance(callee, st.Builtin) or len(callee.types) != len(statement.args):
        state.fault(f"an intrinsic call with {len(statement.args)} arguments and the "
                    f"callee {callee}")
    values = [by_address_or_value(state, operand) if by_address
              else operand_value(state, operand) for operand, _ in statement.args]
    return list(zip(values, callee.types, strict=True))


def _interp(state: FunctionState, statement: st.Call) -> ir.Value:
    """A fresh owned string, from the string builder."""
    return emit_interpolation(state.codegen, operands(state, statement, by_address=True))


def _errno(state: FunctionState, statement: st.Call) -> ir.Value:
    """The `errno` of the calling thread: one call of the location function of the
    platform, then a load of the `int`."""
    if operands(state, statement):
        state.fault("a call of errno with an argument")
    location = declare_errno_location(state.codegen.module)
    return state.builder.load(state.call(location, []))


def _print(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=False)


def _println(state: FunctionState, statement: st.Call) -> ir.Value:
    return _write(state, statement, is_line=True)


def _write(state: FunctionState, statement: st.Call, *, is_line: bool) -> ir.Value:
    """One value to the console, through the print emitter. A value that prints by its
    `Display` form writes that form, a fresh string that the print frees. The call answers
    `~`."""
    parts = operands(state, statement, by_address=True)
    if len(parts) != 1:
        state.fault(f"a print of {len(parts)} values")
    (value, ty), = parts
    codegen = state.codegen
    if ty in _PRINTS_AS_IT_IS or is_numeric_type(ty):
        codegen.runtime.formatting.emit_print_value(value, is_line=is_line, semantic_type=ty)
    else:
        text = emit_value_to_str(codegen, value, ty)
        codegen.runtime.formatting.emit_print_value(text, is_line=is_line,
                                                    semantic_type=BuiltinType.STRING)
        slot = entry_alloca(state.builder, text.type, name="printed")
        state.builder.store(text, slot)
        emit_value_destructor(codegen, slot, BuiltinType.STRING)
    return constant_value(state, k.Blank())


def _two_values(state: FunctionState, statement: st.Call) -> tuple[ir.Value, ir.Value, Type]:
    """The two operands of an `eq` or a `compare`, and their one type."""
    parts = operands(state, statement, by_address=True)
    if len(parts) != 2 or parts[0][1] != parts[1][1]:
        state.fault(f"a contract call of {len(parts)} values of the types "
                    f"{[ty for _, ty in parts]}")
    (left, ty), (right, _) = parts
    return left, right, ty


def _eq(state: FunctionState, statement: st.Call) -> ir.Value:
    """The equality of two values of one type, through the contract seam. A match on a
    string tests each literal arm with it. The answer is a `bool`."""
    left, right, ty = _two_values(state, statement)
    answer = emit_value_eq(state.codegen, left, right, ty)
    return state.builder.zext(answer, state.codegen.types.ll_type(BuiltinType.BOOL))


def _compare(state: FunctionState, statement: st.Call) -> ir.Value:
    """The order of two values of one type, through the contract seam: an `i32`."""
    left, right, ty = _two_values(state, statement)
    return emit_value_compare(state.codegen, left, right, ty)


def _one_value(state: FunctionState, statement: st.Call,
               by_address: bool = False) -> tuple[ir.Value, Type]:
    """The one operand of a `hash` or a `to_str`, and its type."""
    parts = operands(state, statement, by_address)
    if len(parts) != 1:
        state.fault(f"a contract call of {len(parts)} values")
    return parts[0]


def _hash(state: FunctionState, statement: st.Call) -> ir.Value:
    """The `u64` hash of a value, through the one hash seam."""
    value, ty = _one_value(state, statement)
    return emit_value_hash(state.codegen, value, ty)


def _to_str(state: FunctionState, statement: st.Call) -> ir.Value:
    """The string form of a value, through the `Display` seam: a fresh string."""
    value, ty = _one_value(state, statement, by_address=True)
    return emit_value_to_str(state.codegen, value, ty)


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
    return string_value(state.builder, array_data(codegen, address, ty),
                        array_length(codegen, address, ty), owned=1, name="from_bytes")


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


def _hashmap_static(name: str) -> _StaticEmitter:
    """A static of `HashMap@(K, V)`, through its row of the HashMap table."""
    return lambda codegen, values, map_type: emit_hashmap_static(codegen, name, map_type,
                                                                 values)


def _own_static(name: str) -> _StaticEmitter:
    """A static of `Own@(T)`, through its row of the Own table."""
    return lambda codegen, values, own_type: emit_own_static(codegen, name, own_type, values)


#: One row for each intrinsic that the emitter handles, by the name of the intrinsic.
INTRINSICS: dict[str, Callable[[FunctionState, st.Call], ir.Value]] = {
    "f64.from_bits": _from_bits(ir.DoubleType()),
    "f32.from_bits": _from_bits(ir.FloatType()),
    "string.from_bytes": _string_from_bytes,
    "List.new": _container_static(_list_static("new"), 0),
    "List.with_capacity": _container_static(_list_static("with_capacity"), 1),
    "HashMap.new": _container_static(_hashmap_static("new"), 0),
    "Own.alloc": _container_static(_own_static("alloc"), 1),
    "compare": _compare,
    "eq": _eq,
    "errno": _errno,
    "hash": _hash,
    "interp": _interp,
    "print": _print,
    "println": _println,
    "to_str": _to_str,
}
