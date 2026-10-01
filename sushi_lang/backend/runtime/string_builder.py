"""A growable byte buffer that builds one string in linear time.

The derived string form (`Display`) writes a value piece by piece: a name, a field, a
separator. Concatenating the pieces copies the whole prefix at every step, so a long
array would print in quadratic time. The buffer appends instead, and doubles its
capacity when a piece does not fit.

The buffer is `{i8* data, i32 len, i32 cap}` in a stack slot. `emit_push` appends bytes
through one `linkonce_odr` function per module, and `emit_finish` hands the bytes over
as an owned string.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import llvmlite.ir as ir

from sushi_lang.backend.expressions.memory import emit_memcpy_bytes, emit_realloc_call
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


_PUSH_SYMBOL = "__sushi_sb_push"
_MIN_CAPACITY = 16


def buffer_type(codegen: 'LLVMCodegen') -> ir.LiteralStructType:
    return ir.LiteralStructType([ir.PointerType(codegen.types.i8), codegen.types.i32,
                                 codegen.types.i32])


def emit_new(codegen: 'LLVMCodegen') -> ir.Value:
    """An empty buffer in a stack slot of the current function; answers its address."""
    ty = buffer_type(codegen)
    slot = entry_alloca(codegen.builder, ty, name="string_buffer")
    codegen.builder.store(ir.Constant(ty, [ir.Constant(ty.elements[0], None),
                                           ir.Constant(codegen.types.i32, 0),
                                           ir.Constant(codegen.types.i32, 0)]), slot)
    return slot


def emit_push(codegen: 'LLVMCodegen', buffer: ir.Value, data: ir.Value,
              length: ir.Value) -> None:
    """Append `length` bytes at `data` to the buffer."""
    codegen.builder.call(_push_func(codegen), [buffer, data, length])


def emit_push_string(codegen: 'LLVMCodegen', buffer: ir.Value, fat: ir.Value) -> None:
    """Append the bytes of a string value."""
    builder = codegen.builder
    emit_push(codegen, buffer, builder.extract_value(fat, 0, name="piece_data"),
              builder.extract_value(fat, 1, name="piece_len"))


def emit_push_text(codegen: 'LLVMCodegen', buffer: ir.Value, text: str) -> None:
    """Append a piece of text the compiler knows."""
    if text:
        emit_push_string(codegen, buffer, codegen.runtime.strings.emit_string_literal(text))


def emit_finish(codegen: 'LLVMCodegen', buffer: ir.Value) -> ir.Value:
    """The bytes as an owned string. The buffer must not be read after this."""
    builder = codegen.builder
    zero = ir.Constant(codegen.types.i32, 0)
    data = builder.load(builder.gep(buffer, [zero, zero]), name="built_data")
    length = builder.load(builder.gep(buffer, [zero, ir.Constant(codegen.types.i32, 1)]),
                          name="built_len")
    fat = ir.Constant(codegen.types.string_struct, ir.Undefined)
    fat = builder.insert_value(fat, data, 0)
    fat = builder.insert_value(fat, length, 1)
    return builder.insert_value(fat, ir.Constant(codegen.types.i8, 1), 2)


def _push_func(codegen: 'LLVMCodegen') -> ir.Function:
    """`void __sushi_sb_push(buffer*, i8* data, i32 len)`, emitted once per module."""
    cached = codegen._contract_funcs.get(_PUSH_SYMBOL)
    if cached is not None:
        return cached

    i32 = codegen.types.i32
    i8_ptr = ir.PointerType(codegen.types.i8)
    fn = ir.Function(codegen.module, ir.FunctionType(
        ir.VoidType(), [ir.PointerType(buffer_type(codegen)), i8_ptr, i32]),
        name=_PUSH_SYMBOL)
    fn.linkage = "linkonce_odr"
    codegen._contract_funcs[_PUSH_SYMBOL] = fn

    saved_builder, saved_func = codegen.builder, codegen.func
    builder = codegen.builder = ir.IRBuilder(fn.append_basic_block(name="entry"))
    codegen.func = fn
    try:
        buffer, data, length = fn.args
        zero = ir.Constant(i32, 0)
        data_slot = builder.gep(buffer, [zero, zero], name="data_slot")
        len_slot = builder.gep(buffer, [zero, ir.Constant(i32, 1)], name="len_slot")
        cap_slot = builder.gep(buffer, [zero, ir.Constant(i32, 2)], name="cap_slot")
        old_len = builder.load(len_slot, name="old_len")
        cap = builder.load(cap_slot, name="cap")
        needed = builder.add(old_len, length, name="needed")

        with builder.if_then(builder.icmp_signed(">", needed, cap, name="must_grow")):
            doubled = builder.mul(cap, ir.Constant(i32, 2), name="doubled")
            grown = builder.select(builder.icmp_signed(">", doubled, needed), doubled, needed)
            new_cap = builder.select(
                builder.icmp_signed("<", grown, ir.Constant(i32, _MIN_CAPACITY)),
                ir.Constant(i32, _MIN_CAPACITY), grown, name="new_cap")
            new_data = emit_realloc_call(codegen, builder.load(data_slot), new_cap)
            builder.store(new_data, data_slot)
            builder.store(new_cap, cap_slot)

        with builder.if_then(builder.icmp_signed(">", length, zero, name="has_bytes")):
            end = builder.gep(builder.load(data_slot, name="data"), [old_len], name="end")
            emit_memcpy_bytes(codegen, end, data, length)
        builder.store(needed, len_slot)
        builder.ret_void()
    finally:
        codegen.builder, codegen.func = saved_builder, saved_func
    return fn
