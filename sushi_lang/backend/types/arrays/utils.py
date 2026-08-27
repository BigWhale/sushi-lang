"""Helper utilities for dynamic array operations."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def emit_array_literal_elements(codegen: 'LLVMCodegen', elements, element_type):
    """Emit array-literal values as RUNS, deep-copying heap-owning aliases.

    A run carries its count rather than a value per slot, so `from([-1; 32768])` emits one
    value and not 32768 (#446).
    """
    from sushi_lang.backend.types.arrays import runs

    return runs.emit_runs(codegen, elements, element_type)


def alias_element_type(codegen: 'LLVMCodegen', elem):
    """Best-effort semantic type of a bare-Name array-literal element (for alias cloning)."""
    from sushi_lang.semantics.ast import Name
    if isinstance(elem, Name):
        return codegen.memory.get_semantic_type(elem.id)
    return None


def emit_dynamic_array_of_length(codegen: 'LLVMCodegen', element_llvm_type: ir.Type,
                                 length: ir.Value) -> tuple[ir.Value, ir.Value]:
    """A `T[]` of a RUN-TIME length, allocated and sized but not filled (#478, Phase 3).

    Returns the descriptor and the data pointer the caller fills.

    Capacity equals the length, rather than the next power of two a readable length takes:
    the doubling loop is a Python loop and there is nothing to iterate over here. It is safe
    at zero, because `emit_dynamic_array_push` already selects a capacity of one when it
    sees zero, so an empty array grows normally.

    Named rather than inlined into the literal path because it is the allocation a range or
    a repeat of a run-time count needs, and the one a fresh array of a copied RANGE will
    need (#462).
    """
    from sushi_lang.backend.expressions import memory

    element_size = memory.get_element_size_constant(codegen, element_llvm_type)
    total_bytes = codegen.builder.mul(length, element_size, name="total_bytes")

    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    data_ptr = memory.emit_realloc_call(codegen, null_ptr, total_bytes)
    typed_data_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type))

    array_struct = _descriptor(codegen, element_llvm_type, length, length, typed_data_ptr)
    return array_struct, typed_data_ptr


def _descriptor(codegen: 'LLVMCodegen', element_llvm_type: ir.Type, length: ir.Value,
                capacity: ir.Value, data_ptr: ir.Value) -> ir.Value:
    """The `{len, cap, data}` descriptor, built by value."""
    array_struct_type = ir.LiteralStructType([
        codegen.types.i32,
        codegen.types.i32,
        ir.PointerType(element_llvm_type)
    ])
    array_struct = ir.Constant(array_struct_type, ir.Undefined)
    array_struct = codegen.builder.insert_value(array_struct, length, 0)
    array_struct = codegen.builder.insert_value(array_struct, capacity, 1)
    return codegen.builder.insert_value(array_struct, data_ptr, 2)


def create_dynamic_array_from_elements(codegen: 'LLVMCodegen', element_type, element_llvm_type: ir.Type,
                                       elements) -> ir.Value:
    """Create a dynamic array struct value from emitted runs."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend.types.arrays import runs

    initial_len = runs.readable_total(elements)

    if initial_len is None:
        # A run-time length. The counts are summed as values, the capacity equals the sum,
        # and Ruling 2 makes a run-time zero DATA rather than an error -- so there is no
        # short circuit here, and the empty case falls out of a zero-byte allocation.
        length = runs.emit_total_length(codegen, elements)
        array_struct, typed_data_ptr = emit_dynamic_array_of_length(
            codegen, element_llvm_type, length)
        runs.fill_runs(codegen, typed_data_ptr, elements, element_llvm_type)
        return array_struct

    if initial_len == 0:
        zero_i32 = ir.Constant(codegen.types.i32, 0)
        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
        return _descriptor(codegen, element_llvm_type, zero_i32, zero_i32, null_ptr)

    capacity = 1
    while capacity < initial_len:
        capacity *= 2

    # The LLVM ABI ALLOC size, which is the stride GEP uses. A padded type's data size is
    # smaller -- a string fat pointer is 12 bytes of data in a 16-byte slot -- so sizing by
    # data size while GEP strides by alloc size overflows the buffer (#24, #29).
    element_size = memory.get_element_size_constant(codegen, element_llvm_type)
    capacity_val = ir.Constant(codegen.types.i32, capacity)
    total_bytes = codegen.builder.mul(capacity_val, element_size, name="total_bytes")

    null_ptr = ir.Constant(ir.PointerType(codegen.types.i8), None)
    data_ptr = memory.emit_realloc_call(codegen, null_ptr, total_bytes)

    typed_data_ptr = codegen.builder.bitcast(data_ptr, ir.PointerType(element_llvm_type))

    runs.fill_runs(codegen, typed_data_ptr, elements, element_llvm_type)

    return _descriptor(codegen, element_llvm_type,
                       ir.Constant(codegen.types.i32, initial_len),
                       ir.Constant(codegen.types.i32, capacity), typed_data_ptr)
