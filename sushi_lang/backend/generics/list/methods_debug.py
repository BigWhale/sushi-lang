"""List<T> debug method implementation."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
from sushi_lang.semantics.generics.type_display import display_type
import llvmlite.ir as ir
from .types import extract_element_type, get_list_len_ptr, get_list_capacity_ptr, get_list_data_ptr
from sushi_lang.backend.generics.debug_output import (
    emit_debug_string, emit_debug_i32, emit_debug_value
)
from sushi_lang.backend.generics.container_walk import emit_container_walk


def emit_list_debug(
    codegen: Any,
    list_value: ir.Value,
    list_type: StructType
) -> ir.Value:
    """Emit List<T>.debug() -> ~"""
    builder = codegen.builder

    element_type = extract_element_type(list_type, codegen)

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

    def print_element(element_ptr: ir.Value, index: ir.Value) -> None:
        emit_debug_string(codegen, builder, "  [")
        emit_debug_i32(codegen, builder, index)
        emit_debug_string(codegen, builder, "] ")
        element = builder.load(element_ptr, name="element")
        emit_debug_value(codegen, builder, element, element_type)
        emit_debug_string(codegen, builder, "\n")

    emit_container_walk(codegen, data_ptr, length, print_element, prefix="debug")

    emit_debug_string(codegen, builder, "}\n")

    return ir.Constant(codegen.types.i32, 0)
