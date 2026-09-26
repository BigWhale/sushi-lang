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


_DESCRIPTOR_LEN, _DESCRIPTOR_CAP, _DESCRIPTOR_DATA = 0, 1, 2


def _descriptor_field(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    field_index: int,
    name: str,
    builder: 'ir.IRBuilder | None',
) -> 'ir.Value':
    """The one GEP on a `T[]` descriptor, `{i32 len, i32 cap, T* data}`.

    A stdlib generator has a builder and no `LLVMCodegen`: it passes `codegen=None` and
    its `builder`.
    """
    from llvmlite import ir
    if builder is None:
        if codegen is None:
            from sushi_lang.internals.errors import raise_internal_error
            raise_internal_error("CE0000", detail="a descriptor GEP with no codegen needs a builder")
        builder = codegen.builder
    i32 = codegen.types.i32 if codegen is not None else ir.IntType(32)
    zero = ir.Constant(i32, 0)
    return builder.gep(array_struct_ptr, [zero, ir.Constant(i32, field_index)], name=name)


def gep_dynamic_array_len(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    name: str = "len_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'len' field of a `T[]` descriptor."""
    return _descriptor_field(codegen, array_struct_ptr, _DESCRIPTOR_LEN, name, builder)


def gep_dynamic_array_cap(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    name: str = "cap_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'cap' field of a `T[]` descriptor."""
    return _descriptor_field(codegen, array_struct_ptr, _DESCRIPTOR_CAP, name, builder)


def gep_dynamic_array_data(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    name: str = "data_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'data' field of a `T[]` descriptor."""
    return _descriptor_field(codegen, array_struct_ptr, _DESCRIPTOR_DATA, name, builder)
