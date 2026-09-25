"""Centralized GEP (GetElementPtr) utilities for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def gep_struct_field(
    codegen: 'LLVMCodegen',
    struct_ptr: 'ir.Value',
    field_index: int,
    name: str = "",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Create a GEP instruction to access a struct field.

    `builder` is for an emitter whose builder is not `codegen.builder`.
    """
    from llvmlite import ir
    zero = ir.Constant(codegen.types.i32, 0)
    field_idx_const = ir.Constant(codegen.types.i32, field_index)
    return (builder or codegen.builder).gep(struct_ptr, [zero, field_idx_const], name=name)


def gep_array_element(
    codegen: 'LLVMCodegen',
    array_ptr: 'ir.Value',
    index: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction to access an array element."""
    return codegen.builder.gep(array_ptr, [index], name=name)


def gep_fixed_array_element(
    codegen: 'LLVMCodegen',
    array_ptr: 'ir.Value',
    index: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction to access a fixed array element."""
    from llvmlite import ir
    zero = ir.Constant(codegen.types.i32, 0)
    return codegen.builder.gep(array_ptr, [zero, index], name=name)


def gep_dynamic_array_len(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "len_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'len' field of a `T[]` descriptor."""
    return gep_struct_field(codegen, array_struct_ptr, 0, name, builder)


def gep_dynamic_array_cap(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "cap_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'cap' field of a `T[]` descriptor."""
    return gep_struct_field(codegen, array_struct_ptr, 1, name, builder)


def gep_dynamic_array_data(
    codegen: 'LLVMCodegen',
    array_struct_ptr: 'ir.Value',
    name: str = "data_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'data' field of a `T[]` descriptor."""
    return gep_struct_field(codegen, array_struct_ptr, 2, name, builder)


def gep_byte_offset(
    codegen: 'LLVMCodegen',
    ptr: 'ir.Value',
    offset: 'ir.Value',
    name: str = ""
) -> 'ir.Value':
    """Create a GEP instruction for byte-level pointer arithmetic."""
    return codegen.builder.gep(ptr, [offset], name=name)
