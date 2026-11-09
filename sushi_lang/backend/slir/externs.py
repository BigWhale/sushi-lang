"""The call of a C function: each operand across the C boundary (docs/design/ir.md 8.6).

The declaration round declared the C function of each declaration under its link name, with
`var_arg` for a variadic one (`codegen.external_funcs`): a symbol that the compiler declared
first with other types is a callee of the type of the declaration (#1099). A `string`
crosses as the copy of the one C-string seam (`emit_cstr_arg`), which the call frees right
after it returns. A byte buffer (`u8[]`) crosses as the pointer
to its first byte: from the descriptor at its address for an `Inout` or an `InGuaranteed`
operand, from its value for a `Guaranteed` one. A value of the variadic tail gets the C
promotion: a narrow integer and a `bool` to `i32`, an `f32` to `f64`. A C function that
answers `~` writes `~` into the place of the call.

A `ptr` crosses as itself. A `Maybe@(ptr)` or a `Maybe@(string)` argument crosses as a null
pointer for `None` and as its marshalled payload for `Some`. The answer becomes a Sushi value
through `foreign.py`. The emitter reads the answer before it frees the C
copies, because C can answer a pointer into one of them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend import enum_utils
from sushi_lang.backend.expressions.calls.utils import emit_cstr_arg, free_cstr_args
from sushi_lang.backend.gep_utils import gep_dynamic_array_data
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.slir.foreign import c_answer
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    constant_value, operand_address, operand_value, place_address, type_of_operand,
)
from sushi_lang.semantics.ffi_boundary import is_byte_buffer, nullable_payload
from sushi_lang.semantics.param_modes import Convention
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir import places as p
from sushi_lang.semantics.slir import statements as st
from sushi_lang.semantics.typesys import BuiltinType, EnumType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.externals import ExternalSig

#: The types that a C promotion widens with a zero extension.
_UNSIGNED = frozenset({BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
                       BuiltinType.BOOL})


def emit_extern(state: FunctionState, statement: st.Call, direct: st.Direct,
                extern: ExternalSig) -> None:
    callee = state.codegen.external_funcs.get((extern.namespace, extern.name))
    if callee is None:
        state.fault(f"the module declares no C function '{extern.link_name}'")
    function_type = callee.type.pointee
    fixed = len(function_type.args)
    copies: list[ir.Value] = []
    args = [_crossing(state, operand, convention, copies, tail=index >= fixed)
            for index, (operand, convention) in enumerate(statement.args)]
    if [arg.type for arg in args[:fixed]] != list(function_type.args):
        state.fault(f"the call of '{direct.symbol}' does not agree with its declaration "
                    f"{function_type}")
    value = state.call(callee, args)
    if isinstance(function_type.return_type, ir.VoidType):
        value = constant_value(state, k.Blank())
    else:
        value = c_answer(state, value, extern.ret_type)
    free_cstr_args(state.codegen, copies)
    state.builder.store(value, place_address(state, statement.dest))


def _crossing(state: FunctionState, operand: p.Operand, convention: Convention,
              copies: list[ir.Value], *, tail: bool) -> ir.Value:
    """The C value of one operand."""
    ty = type_of_operand(state, operand)
    if is_byte_buffer(ty):
        if convention.by_address:
            return state.builder.load(gep_dynamic_array_data(
                state.codegen, operand_address(state, operand), builder=state.builder))
        return state.builder.extract_value(operand_value(state, operand), 2)
    if convention.by_address:
        state.fault(f"a {convention.value} argument of the type {ty} crosses to C")
    value = operand_value(state, operand)
    if ty == BuiltinType.STRING:
        copy = emit_cstr_arg(state.codegen, value)
        copies.append(copy)
        return copy
    payload = nullable_payload(ty)
    if isinstance(ty, EnumType) and payload is not None:
        return _nullable_argument(state, value, ty, payload, copies)
    return _promoted(state, value, ty) if tail else value


def _nullable_argument(state: FunctionState, maybe: ir.Value, maybe_type: EnumType,
                       payload: Type, copies: list[ir.Value]) -> ir.Value:
    """`None` crosses as a null pointer, `Some(v)` as the marshalled `v`. The C copy of a
    `string` payload is freed after the call; `free` of a null pointer does nothing."""
    builder = state.builder
    null = ir.Constant(ir.PointerType(ir.IntType(8)), None)
    is_some = builder.icmp_unsigned(
        "==", builder.extract_value(maybe, 0),
        ir.Constant(ir.IntType(32), maybe_type.get_variant_index("Some")))
    origin = builder.block
    some_block = builder.append_basic_block("ffi_arg_some")
    merge_block = builder.append_basic_block("ffi_arg_merge")
    builder.cbranch(is_some, some_block, merge_block)
    builder.position_at_end(some_block)
    value = _payload(state, maybe, payload)
    if payload == BuiltinType.STRING:
        value = emit_cstr_arg(state.codegen, value)
    value = builder.bitcast(value, null.type)
    some_end = builder.block
    builder.branch(merge_block)
    builder.position_at_end(merge_block)
    pointer = builder.phi(null.type)
    pointer.add_incoming(null, origin)
    pointer.add_incoming(value, some_end)
    if payload == BuiltinType.STRING:
        copies.append(pointer)
    return pointer


def _payload(state: FunctionState, maybe: ir.Value, payload: Type) -> ir.Value:
    """The `Some` payload of a Maybe value, through the one layout of a payload."""
    codegen = state.codegen
    slot = entry_alloca(state.builder, maybe.type, "ffi_maybe")
    state.builder.store(maybe, slot)
    offset = codegen.types.payload_field_offsets([payload])[0]
    return enum_utils.unpack_variant_field(codegen, enum_utils.get_data_ptr(codegen, slot),
                                           payload, offset)


def _promoted(state: FunctionState, value: ir.Value, ty: object) -> ir.Value:
    """The C default promotion of one value of a variadic tail."""
    builder = state.builder
    if isinstance(value.type, ir.FloatType):
        return builder.fpext(value, ir.DoubleType())
    if isinstance(value.type, ir.IntType) and value.type.width < 32:
        if ty in _UNSIGNED:
            return builder.zext(value, ir.IntType(32))
        return builder.sext(value, ir.IntType(32))
    return value
