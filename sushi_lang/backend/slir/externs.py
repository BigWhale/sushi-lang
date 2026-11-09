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
pointer for `None` and as its marshalled payload for `Some`. A nullable answer is `None` for
a null pointer; a plain `ptr` or `string` answer that is null ends the program with RE2025.
A `string` answer is an owned copy. The emitter reads the answer before it frees the C
copies, because C can answer a pointer into one of them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.expressions.calls.utils import emit_cstr_arg, free_cstr_args
from sushi_lang.backend.gep_utils import gep_dynamic_array_data
from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.slir.state import FunctionState
from sushi_lang.backend.slir.values import (
    constant_value, operand_address, operand_value, place_address, type_of_operand,
)
from sushi_lang.semantics.ffi_boundary import is_byte_buffer, is_pointer_value, nullable_payload
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
        value = _answer(state, value, extern.ret_type)
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
    """The `Some` payload of a Maybe value, at offset 0 of its data."""
    builder = state.builder
    data = builder.extract_value(maybe, 1)
    slot = entry_alloca(builder, data.type, "ffi_maybe_data")
    builder.store(data, slot)
    typed = builder.bitcast(slot, ir.PointerType(state.codegen.types.ll_type(payload)))
    return builder.load(typed)


def _answer(state: FunctionState, raw: ir.Value, ty: Type | None) -> ir.Value:
    """The Sushi value of the answer of a C function."""
    payload = nullable_payload(ty)
    if payload is not None:
        return _nullable_answer(state, raw, payload)
    if is_pointer_value(ty):
        _end_on_null(state, raw)
        return _pointer_value(state, raw, ty)
    return raw


def _pointer_value(state: FunctionState, raw: ir.Value, ty: Type | None) -> ir.Value:
    """A `char*` answer is an owned copy; a `ptr` is the pointer itself."""
    if ty == BuiltinType.STRING:
        return state.codegen.runtime.strings.emit_cstr_to_owned_fat_pointer(raw)
    return raw


def _end_on_null(state: FunctionState, raw: ir.Value) -> None:
    """RE2025: a declaration that says non-null got a null pointer."""
    builder = state.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None))
    trap_block = builder.append_basic_block("ffi_null_trap")
    ok_block = builder.append_basic_block("ffi_non_null")
    builder.cbranch(is_null, trap_block, ok_block)
    builder.position_at_end(trap_block)
    state.codegen.runtime.errors.emit_runtime_error("RE2025")
    builder.unreachable()
    builder.position_at_end(ok_block)


def _nullable_answer(state: FunctionState, raw: ir.Value, payload: Type) -> ir.Value:
    """A null pointer is `Maybe.None`; any other pointer is `Maybe.Some` of its value."""
    codegen = state.codegen
    builder = state.builder
    is_null = builder.icmp_unsigned("==", raw, ir.Constant(raw.type, None))
    none_block = builder.append_basic_block("ffi_ret_none")
    some_block = builder.append_basic_block("ffi_ret_some")
    merge_block = builder.append_basic_block("ffi_ret_merge")
    builder.cbranch(is_null, none_block, some_block)
    builder.position_at_end(none_block)
    none_value = emit_maybe_none(codegen, payload)
    none_end = builder.block
    builder.branch(merge_block)
    builder.position_at_end(some_block)
    some_value = emit_maybe_some(codegen, payload, _pointer_value(state, raw, payload))
    some_end = builder.block
    builder.branch(merge_block)
    builder.position_at_end(merge_block)
    result = builder.phi(none_value.type)
    result.add_incoming(none_value, none_end)
    result.add_incoming(some_value, some_end)
    return result


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
