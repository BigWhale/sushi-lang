"""Centralized GEP (GetElementPtr) utilities for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

from sushi_lang.backend.stored_width import as_stored_i64, as_surface_i32

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


def _builder_of(codegen: 'LLVMCodegen | None', builder: 'ir.IRBuilder | None') -> 'ir.IRBuilder':
    """The builder of an emit. A stdlib generator has a builder and no `LLVMCodegen`: it
    passes `codegen=None` and its `builder`."""
    if builder is not None:
        return builder
    if codegen is None:
        from sushi_lang.internals.errors import raise_internal_error
        raise_internal_error("CE0000", detail="a descriptor access with no codegen needs a builder")
    return codegen.builder


def _descriptor_field(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    field_index: int,
    name: str,
    builder: 'ir.IRBuilder | None',
) -> 'ir.Value':
    """The one GEP on a `{i64 len, i64 cap, T* data}` header: a `T[]` descriptor or a
    `List@(T)`."""
    from llvmlite import ir
    i32 = ir.IntType(32)
    zero = ir.Constant(i32, 0)
    return _builder_of(codegen, builder).gep(
        array_struct_ptr, [zero, ir.Constant(i32, field_index)], name=name)


def _load_count(codegen, header_ptr, field, name, builder) -> 'ir.Value':
    b = _builder_of(codegen, builder)
    field_ptr = _descriptor_field(codegen, header_ptr, field, "", b)
    return as_surface_i32(b, b.load(field_ptr), name)


def _store_count(codegen, header_ptr, value, field, builder) -> None:
    b = _builder_of(codegen, builder)
    b.store(as_stored_i64(b, value), _descriptor_field(codegen, header_ptr, field, "", b))


def load_len(codegen: 'LLVMCodegen | None', header_ptr: 'ir.Value', name: str = "len",
             builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The length of the `T[]` or `List@(T)` at `header_ptr`, as an i32."""
    return _load_count(codegen, header_ptr, _DESCRIPTOR_LEN, name, builder)


def load_cap(codegen: 'LLVMCodegen | None', header_ptr: 'ir.Value', name: str = "cap",
             builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The capacity of the `T[]` or `List@(T)` at `header_ptr`, as an i32."""
    return _load_count(codegen, header_ptr, _DESCRIPTOR_CAP, name, builder)


def store_len(codegen: 'LLVMCodegen | None', header_ptr: 'ir.Value', value: 'ir.Value',
              builder: 'ir.IRBuilder | None' = None) -> None:
    """Write the length of the `T[]` or `List@(T)` at `header_ptr`."""
    _store_count(codegen, header_ptr, value, _DESCRIPTOR_LEN, builder)


def store_cap(codegen: 'LLVMCodegen | None', header_ptr: 'ir.Value', value: 'ir.Value',
              builder: 'ir.IRBuilder | None' = None) -> None:
    """Write the capacity of the `T[]` or `List@(T)` at `header_ptr`."""
    _store_count(codegen, header_ptr, value, _DESCRIPTOR_CAP, builder)


def extract_len(codegen: 'LLVMCodegen | None', header: 'ir.Value', name: str = "len",
                builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The length of the `T[]` or `List@(T)` VALUE `header`, as an i32."""
    b = _builder_of(codegen, builder)
    return as_surface_i32(b, b.extract_value(header, _DESCRIPTOR_LEN), name)


def extract_cap(codegen: 'LLVMCodegen | None', header: 'ir.Value', name: str = "cap",
                builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The capacity of the `T[]` or `List@(T)` VALUE `header`, as an i32."""
    b = _builder_of(codegen, builder)
    return as_surface_i32(b, b.extract_value(header, _DESCRIPTOR_CAP), name)


def insert_len(codegen: 'LLVMCodegen | None', header: 'ir.Value', value: 'ir.Value',
               builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The VALUE `header` with its length replaced by `value`."""
    b = _builder_of(codegen, builder)
    return b.insert_value(header, as_stored_i64(b, value), _DESCRIPTOR_LEN)


def insert_cap(codegen: 'LLVMCodegen | None', header: 'ir.Value', value: 'ir.Value',
               builder: 'ir.IRBuilder | None' = None) -> 'ir.Value':
    """The VALUE `header` with its capacity replaced by `value`."""
    b = _builder_of(codegen, builder)
    return b.insert_value(header, as_stored_i64(b, value), _DESCRIPTOR_CAP)


def gep_dynamic_array_data(
    codegen: 'LLVMCodegen | None',
    array_struct_ptr: 'ir.Value',
    name: str = "data_ptr",
    builder: 'ir.IRBuilder | None' = None,
) -> 'ir.Value':
    """Get pointer to the 'data' field of a `T[]` descriptor."""
    return _descriptor_field(codegen, array_struct_ptr, _DESCRIPTOR_DATA, name, builder)
