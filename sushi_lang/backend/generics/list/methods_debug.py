"""List<T> debug method implementation."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
from sushi_lang.semantics.generics.type_display import display_type
import llvmlite.ir as ir
from .types import extract_element_type, get_list_len_ptr, get_list_capacity_ptr, get_list_data_ptr
from sushi_lang.backend.constants.llvm_values import ZERO_I32, ONE_I32
from sushi_lang.backend.generics.debug_output import (
    emit_debug_string, emit_debug_i32, emit_debug_value
)
from sushi_lang.backend.memory.allocas import entry_alloca


def emit_list_debug(
    codegen: Any,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.debug() -> ~"""
    builder = codegen.builder

    element_type = extract_element_type(list_type, codegen)

    zero_i32 = ZERO_I32
    one_i32 = ONE_I32

    len_ptr = get_list_len_ptr(builder, list_value)
    capacity_ptr = get_list_capacity_ptr(builder, list_value)
    data_ptr_ptr = get_list_data_ptr(builder, list_value)

    length = builder.load(len_ptr, name="length")
    capacity = builder.load(capacity_ptr, name="capacity")
    data_ptr = builder.load(data_ptr_ptr, name="data_ptr")

    # Print header: "List@(T) {". The element type goes through display_type, not
    # str(): a monomorphized type's str() is its interned `<...>` identity name, so
    # interpolating it directly leaks the retired syntax for a nested generic
    # element ("List@(List<i32>)" instead of "List@(List@(i32))").
    header_str = f"List@({display_type(element_type)}) {{\n"
    emit_debug_string(codegen, builder, header_str)

    emit_debug_string(codegen, builder, "  len: ")
    emit_debug_i32(codegen, builder, length)
    emit_debug_string(codegen, builder, ", capacity: ")
    emit_debug_i32(codegen, builder, capacity)
    emit_debug_string(codegen, builder, "\n")

    i = entry_alloca(builder, codegen.types.i32, name="i")
    builder.store(zero_i32, i)

    loop_cond_bb = builder.append_basic_block(name="debug_loop_cond")
    loop_body_bb = builder.append_basic_block(name="debug_loop_body")
    loop_end_bb = builder.append_basic_block(name="debug_loop_end")

    builder.branch(loop_cond_bb)

    builder.position_at_end(loop_cond_bb)
    i_val = builder.load(i, name="i_val")
    cond = builder.icmp_unsigned("<", i_val, length, name="loop_cond")
    builder.cbranch(cond, loop_body_bb, loop_end_bb)

    builder.position_at_end(loop_body_bb)
    i_val = builder.load(i, name="i_val")

    emit_debug_string(codegen, builder, "  [")
    emit_debug_i32(codegen, builder, i_val)
    emit_debug_string(codegen, builder, "] ")

    element_ptr = builder.gep(data_ptr, [i_val], name="element_ptr")
    element = builder.load(element_ptr, name="element")

    emit_debug_value(codegen, builder, element, element_type)
    emit_debug_string(codegen, builder, "\n")

    i_next = builder.add(i_val, one_i32, name="i_next")
    builder.store(i_next, i)
    builder.branch(loop_cond_bb)

    builder.position_at_end(loop_end_bb)

    emit_debug_string(codegen, builder, "}\n")

    return ir.Constant(codegen.types.i32, 0)
