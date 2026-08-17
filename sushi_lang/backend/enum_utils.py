"""Enum manipulation utilities for LLVM codegen."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple

from llvmlite import ir

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def extract_enum_tag(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    name: str = "tag"
) -> ir.Value:
    """Extract the discriminant tag (field 0) from an enum value."""
    return codegen.builder.extract_value(enum_value, 0, name=name)


def extract_enum_data(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    name: str = "data"
) -> ir.Value:
    """Extract the data array (field 1) from an enum value."""
    return codegen.builder.extract_value(enum_value, 1, name=name)


def compare_enum_tags(
    codegen: 'LLVMCodegen',
    tag1: ir.Value,
    tag2: ir.Value,
    signed: bool = True,
    name: str = "tags_equal"
) -> ir.Value:
    """Compare two enum tags for equality."""
    if signed:
        return codegen.builder.icmp_signed("==", tag1, tag2, name=name)
    else:
        return codegen.builder.icmp_unsigned("==", tag1, tag2, name=name)


def check_enum_variant(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    variant_index: int,
    signed: bool = True,
    name: str = "is_variant"
) -> ir.Value:
    """Check if an enum value matches a specific variant by index."""
    tag = extract_enum_tag(codegen, enum_value, name=f"{name}_tag")
    expected_tag = ir.Constant(codegen.types.i32, variant_index)
    return compare_enum_tags(codegen, tag, expected_tag, signed=signed, name=name)


def construct_enum_variant(
    codegen: 'LLVMCodegen',
    enum_llvm_type: ir.Type,
    variant_index: int,
    data: ir.Value = None,
    name_prefix: str = "enum"
) -> ir.Value:
    """Construct an enum value with a specific variant tag and optional data."""
    # Start with undefined enum struct
    enum_value = ir.Constant(enum_llvm_type, ir.Undefined)

    # Insert discriminant tag
    tag = ir.Constant(codegen.types.i32, variant_index)
    enum_value = codegen.builder.insert_value(
        enum_value, tag, 0,
        name=f"{name_prefix}_tag"
    )

    # Insert data if provided
    if data is not None:
        enum_value = codegen.builder.insert_value(
            enum_value, data, 1,
            name=f"{name_prefix}_with_data"
        )

    return enum_value


def set_enum_data(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    data: ir.Value,
    name: str = "enum_with_data"
) -> ir.Value:
    """Set the data field (field 1) of an enum value."""
    return codegen.builder.insert_value(enum_value, data, 1, name=name)


def compare_tag_to_const(
    codegen: 'LLVMCodegen',
    tag: ir.Value,
    const_value: int,
    signed: bool = True,
    name: str = "tag_matches"
) -> ir.Value:
    """Compare an enum tag to a constant variant index."""
    expected_tag = ir.Constant(codegen.types.i32, const_value)
    return compare_enum_tags(codegen, tag, expected_tag, signed=signed, name=name)


def unpack_variant_field(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_type: 'Type',
    offset: int,
    name: str = "field"
) -> Tuple[ir.Value, int]:
    """Unpack a single field from enum variant data at a given offset."""
    from sushi_lang.backend.types.core.sizing import align_up
    field_llvm_type = codegen.types.ll_type(field_type)
    field_size = codegen.types.get_type_size_bytes(field_type)
    offset = align_up(offset, codegen.types.get_type_alignment(field_type))

    if offset > 0:
        field_ptr = codegen.builder.gep(
            data_ptr,
            [ir.Constant(codegen.types.i32, offset)],
            inbounds=True,
            name=f"{name}_offset_ptr"
        )
    else:
        field_ptr = data_ptr

    typed_ptr = codegen.builder.bitcast(
        field_ptr,
        ir.PointerType(field_llvm_type),
        name=f"{name}_typed_ptr"
    )

    # Natural alignment: the payload base is 8-aligned (the enum's data member is an
    # i64 array, #300 phase 2) and the offset was aligned above, so the access needs no
    # `align=1` any more -- that workaround existed for the packed layout (#145).
    value = codegen.builder.load(typed_ptr, name=name)
    return value, offset + field_size


def unpack_all_variant_fields(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_types: List['Type'],
    name_prefix: str = "field"
) -> List[ir.Value]:
    """Unpack all fields from enum variant data."""
    values = []
    offset = 0

    for i, field_type in enumerate(field_types):
        value, offset = unpack_variant_field(
            codegen, data_ptr, field_type, offset,
            name=f"{name_prefix}_{i}"
        )
        values.append(value)

    return values


def pack_variant_field(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_value: ir.Value,
    field_type: 'Type',
    offset: int,
    name: str = "field"
) -> int:
    """Pack a single field into enum variant data at a given offset."""
    from sushi_lang.backend.types.core.sizing import align_up
    field_llvm_type = codegen.types.ll_type(field_type)
    field_size = codegen.types.get_type_size_bytes(field_type)
    offset = align_up(offset, codegen.types.get_type_alignment(field_type))

    if offset > 0:
        field_ptr = codegen.builder.gep(
            data_ptr,
            [ir.Constant(codegen.types.i32, offset)],
            inbounds=True,
            name=f"{name}_offset_ptr"
        )
    else:
        field_ptr = data_ptr

    typed_ptr = codegen.builder.bitcast(
        field_ptr,
        ir.PointerType(field_llvm_type),
        name=f"{name}_typed_ptr"
    )

    # Natural alignment: the payload base is 8-aligned and the offset was aligned above
    # (#300 phase 2), so the `align=1` workaround for the packed layout (#145) is gone.
    codegen.builder.store(field_value, typed_ptr)
    return offset + field_size


def pack_all_variant_fields(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_values: List[ir.Value],
    field_types: List['Type'],
    name_prefix: str = "field"
) -> None:
    """Pack all fields into enum variant data."""
    if len(field_values) != len(field_types):
        raise ValueError(f"Mismatch: {len(field_values)} values vs {len(field_types)} types")

    offset = 0
    for i, (value, field_type) in enumerate(zip(field_values, field_types, strict=True)):
        offset = pack_variant_field(
            codegen, data_ptr, value, field_type, offset,
            name=f"{name_prefix}_{i}"
        )


def get_data_ptr(
    codegen: 'LLVMCodegen',
    enum_ptr: ir.Value,
    name: str = "data_ptr"
) -> ir.Value:
    """Get pointer to the data array of an enum stored in memory."""
    data_field_ptr = codegen.builder.gep(
        enum_ptr,
        [ir.Constant(codegen.types.i32, 0), ir.Constant(codegen.types.i32, 1)],
        inbounds=True,
        name=f"{name}_gep"
    )
    return codegen.builder.bitcast(
        data_field_ptr,
        ir.PointerType(codegen.types.i8),
        name=name
    )
