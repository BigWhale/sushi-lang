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


def create_dynamic_array_from_elements(codegen: 'LLVMCodegen', element_type, element_llvm_type: ir.Type,
                                       elements) -> ir.Value:
    """Create a dynamic array struct value from emitted runs."""
    from sushi_lang.backend.expressions import memory
    from sushi_lang.backend.types.arrays import runs

    initial_len = runs.total_elements(elements)
    if initial_len == 0:
        zero_i32 = ir.Constant(codegen.types.i32, 0)
        null_ptr = ir.Constant(ir.PointerType(element_llvm_type), None)
        array_struct_type = ir.LiteralStructType([
            codegen.types.i32,
            codegen.types.i32,
            ir.PointerType(element_llvm_type)
        ])
        array_struct = ir.Constant(array_struct_type, ir.Undefined)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 0)
        array_struct = codegen.builder.insert_value(array_struct, zero_i32, 1)
        array_struct = codegen.builder.insert_value(array_struct, null_ptr, 2)
        return array_struct

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

    array_struct_type = ir.LiteralStructType([
        codegen.types.i32,
        codegen.types.i32,
        ir.PointerType(element_llvm_type)
    ])

    len_val = ir.Constant(codegen.types.i32, initial_len)
    cap_val = ir.Constant(codegen.types.i32, capacity)

    array_struct = ir.Constant(array_struct_type, ir.Undefined)
    array_struct = codegen.builder.insert_value(array_struct, len_val, 0)
    array_struct = codegen.builder.insert_value(array_struct, cap_val, 1)
    array_struct = codegen.builder.insert_value(array_struct, typed_data_ptr, 2)

    return array_struct
