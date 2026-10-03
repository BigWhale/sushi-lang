"""The derived string form of ONE value (`Display`), by its semantic type.

An interpolation hole, `print` and `println`, a field of a derived string form and an
explicit `to_str()` all ask this module, so a type prints the same in every position.
The order is the method-resolution order: a `Display` implementation wins, then the
derived function, then the primitive rule.

The form is the one a named construction writes: `Point(x: 1, y: 2)`, `Shape.Circle(5)`,
`Colour.Red`, `[1, 2, 3]`. A string HELD in a type prints in quotes, so that its bounds
are visible; a hole of its own prints it bare and never reaches here. An `Own@(T)`
prints its payload.

A composite type gets one out-of-line `linkonce_odr` function that appends its form to
a buffer (`runtime/string_builder.py`), so a nested value is never copied twice and a
type that reaches itself prints by a call of the function it is in. The function writes
numbers with `sprintf` into a stack slot: a conversion helper that allocates registers
its buffer with the print frame of the CALL SITE, which is another function.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import llvmlite.ir as ir

from sushi_lang.backend import enum_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.generics.list.types import get_list_data_ptr, get_list_len_ptr
from sushi_lang.backend.lifecycle import lifecycle_symbol
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.runtime import string_builder as sb
from sushi_lang.backend.types.contracts import contract_override
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.contracts import DISPLAY
from sushi_lang.semantics.generics.tuples import is_tuple_type
from sushi_lang.semantics.type_predicates import is_instance_of
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, StructType, Type)

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


_NUMBER_FORMATS = {
    BuiltinType.I8: "i32", BuiltinType.I16: "i32", BuiltinType.I32: "i32",
    BuiltinType.I64: "i64", BuiltinType.U8: "u32", BuiltinType.U16: "u32",
    BuiltinType.U32: "u32", BuiltinType.U64: "u64", BuiltinType.F32: "f32",
    BuiltinType.F64: "f64",
}
_NUMBER_SLOT = 64


def emit_value_to_str(codegen: 'LLVMCodegen', value: ir.Value, ty: Type) -> ir.Value:
    """The string form of a value that is not a primitive, as a fresh owned string."""
    override = contract_override(codegen, ty, DISPLAY)
    if override is not None:
        from sushi_lang.backend.types.contracts import call_override
        return call_override(codegen, override, [value])
    buffer = sb.emit_new(codegen)
    emit_value_fmt(codegen, value, ty, buffer)
    return sb.emit_finish(codegen, buffer)


def emit_value_fmt(codegen: 'LLVMCodegen', value: ir.Value, ty: Type,
                   buffer: ir.Value) -> None:
    """Append the string form of a HELD value to the buffer."""
    override = contract_override(codegen, ty, DISPLAY)
    if override is not None:
        from sushi_lang.backend.destructors import emit_string_destructor_from_value
        from sushi_lang.backend.types.contracts import call_override
        text = call_override(codegen, override, [value])
        sb.emit_push_string(codegen, buffer, text)
        emit_string_destructor_from_value(codegen, text)
        return
    if isinstance(ty, BuiltinType):
        _primitive_fmt(codegen, value, ty, buffer)
        return
    codegen.builder.call(_fmt_func(codegen, ty), [value, buffer])


def _primitive_fmt(codegen: 'LLVMCodegen', value: ir.Value, ty: BuiltinType,
                   buffer: ir.Value) -> None:
    builder = codegen.builder
    if ty == BuiltinType.STRING:
        sb.emit_push_text(codegen, buffer, '"')
        sb.emit_push_string(codegen, buffer, value)
        sb.emit_push_text(codegen, buffer, '"')
        return
    if ty == BuiltinType.BLANK:
        sb.emit_push_text(codegen, buffer, "~")
        return
    if ty == BuiltinType.BOOL:
        sb.emit_push_string(codegen, buffer,
                            codegen.runtime.formatting.emit_bool_to_string(value))
        return
    name = _NUMBER_FORMATS.get(ty)
    if name is None:
        raise_internal_error("CE0022", type=str(ty))
    if ty == BuiltinType.F32:
        value = builder.fpext(value, codegen.types.f64)
    elif ty in (BuiltinType.I8, BuiltinType.I16):
        value = builder.sext(value, codegen.types.i32)
    elif ty in (BuiltinType.U8, BuiltinType.U16):
        value = builder.zext(value, codegen.types.i32)
    slot = entry_alloca(builder, ir.ArrayType(codegen.types.i8, _NUMBER_SLOT),
                        name="number_text")
    zero = ir.Constant(codegen.types.i32, 0)
    text = builder.gep(slot, [zero, zero], name="number_text_ptr")
    written = builder.call(codegen.runtime.libc_strings.sprintf,
                           [text, codegen.runtime.formatting._get_format_string(name), value],
                           name="number_len")
    sb.emit_push(codegen, buffer, text, written)


def _display_name(ty: Type) -> str:
    """A type's name as the source writes it at a construction: the base, no arguments."""
    return getattr(ty, "generic_base", None) or ty.name


def _fmt_func(codegen: 'LLVMCodegen', ty: Type) -> ir.Function:
    """Get (or lazily emit) `void __sushi_fmt_<k>(T value, buffer*)` for one type.

    Cached before the body is emitted, as `contracts._contract_func` is, so a nested
    value of the same type is a call of this function.
    """
    symbol = lifecycle_symbol("__sushi_fmt_", ty)
    cached = codegen._contract_funcs.get(symbol)
    if cached is not None:
        return cached

    llvm_type = codegen.types.ll_type(ty)
    fn = ir.Function(codegen.module, ir.FunctionType(
        ir.VoidType(), [llvm_type, ir.PointerType(sb.buffer_type(codegen))]), name=symbol)
    fn.linkage = "linkonce_odr"
    codegen._contract_funcs[symbol] = fn

    saved_builder, saved_func = codegen.builder, codegen.func
    codegen.builder = ir.IRBuilder(fn.append_basic_block(name="entry"))
    codegen.func = fn
    try:
        value, buffer = fn.args
        _emit_fmt_body(codegen, value, ty, buffer)
        codegen.builder.ret_void()
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn


def _emit_fmt_body(codegen: 'LLVMCodegen', value: ir.Value, ty: Type,
                   buffer: ir.Value) -> None:
    builder = codegen.builder
    if isinstance(ty, EnumType):
        _enum_fmt(codegen, value, ty, buffer)
    elif isinstance(ty, StructType) and is_instance_of(ty, "Own"):
        zero = ir.Constant(codegen.types.i32, 0)
        slot = entry_alloca(builder, value.type, name="own_view")
        builder.store(value, slot)
        pointer = builder.load(builder.gep(slot, [zero, zero]), name="own_ptr")
        emit_value_fmt(codegen, builder.load(pointer, name="own_payload"),
                       ty.generic_args[0], buffer)
    elif isinstance(ty, StructType) and is_instance_of(ty, "List"):
        slot = entry_alloca(builder, value.type, name="list_view")
        builder.store(value, slot)
        _sequence_fmt(codegen, ty.generic_args[0],
                      builder.load(get_list_data_ptr(builder, slot), name="list_data"),
                      builder.load(get_list_len_ptr(builder, slot), name="list_len"),
                      buffer)
    elif isinstance(ty, StructType):
        # A tuple prints its elements alone: `(1, "a")`, the spelling of its literal.
        tuple_form = is_tuple_type(ty)
        sb.emit_push_text(codegen, buffer, "(" if tuple_form else f"{_display_name(ty)}(")
        for index, (name, field_type) in enumerate(ty.fields):
            prefix = ", " if index else ""
            sb.emit_push_text(codegen, buffer, prefix if tuple_form else f"{prefix}{name}: ")
            emit_value_fmt(codegen, builder.extract_value(value, index), field_type, buffer)
        sb.emit_push_text(codegen, buffer, ")")
    elif isinstance(ty, DynamicArrayType):
        _sequence_fmt(codegen, ty.base_type, builder.extract_value(value, 2),
                      builder.extract_value(value, 0), buffer)
    elif isinstance(ty, ArrayType):
        zero = ir.Constant(codegen.types.i32, 0)
        slot = entry_alloca(builder, value.type, name="array_view")
        builder.store(value, slot)
        _sequence_fmt(codegen, ty.base_type, builder.gep(slot, [zero, zero]),
                      ir.Constant(codegen.types.i32, ty.size), buffer)
    else:
        raise_internal_error("CE0022", type=str(ty))


def _sequence_fmt(codegen: 'LLVMCodegen', element_type: Type, data: ir.Value,
                  count: ir.Value, buffer: ir.Value) -> None:
    """`[a, b, c]`: the elements in order, a comma between two."""
    sb.emit_push_text(codegen, buffer, "[")

    def on_element(element_ptr: ir.Value, index: ir.Value) -> None:
        builder = codegen.builder
        not_first = builder.icmp_signed(">", index, ir.Constant(index.type, 0))
        with builder.if_then(not_first):
            sb.emit_push_text(codegen, buffer, ", ")
        emit_value_fmt(codegen, builder.load(element_ptr, name="element"), element_type,
                       buffer)

    emit_container_walk(codegen, data, count, on_element, null_guard=True,
                        prefix="display_walk")
    sb.emit_push_text(codegen, buffer, "]")


def _enum_fmt(codegen: 'LLVMCodegen', value: ir.Value, ty: EnumType,
              buffer: ir.Value) -> None:
    """`Enum.Variant`, and the payload by position in parentheses."""
    builder = codegen.builder
    tag = enum_utils.extract_enum_tag(codegen, value, name="display_tag")
    data = None
    if any(variant.associated_types for variant in ty.variants):
        slot = entry_alloca(builder, value.type, name="display_enum")
        builder.store(value, slot)
        data = enum_utils.get_data_ptr(codegen, slot, "display_data")
    done = builder.append_basic_block(name="display_done")
    switch = builder.switch(tag, done)
    for index, variant in enumerate(ty.variants):
        case = builder.append_basic_block(name=f"display_{variant.name}")
        switch.add_case(ir.Constant(tag.type, index), case)
        builder.position_at_end(case)
        sb.emit_push_text(codegen, buffer, f"{_display_name(ty)}.{variant.name}")
        if variant.associated_types:
            sb.emit_push_text(codegen, buffer, "(")
            fields = enum_utils.unpack_all_variant_fields(
                codegen, data, variant.associated_types, f"display_{variant.name}")
            for position, (field, field_type) in enumerate(
                    zip(fields, variant.associated_types, strict=True)):
                if position:
                    sb.emit_push_text(codegen, buffer, ", ")
                emit_value_fmt(codegen, field, field_type, buffer)
            sb.emit_push_text(codegen, buffer, ")")
        builder.branch(done)
    builder.position_at_end(done)
