"""
Enum manipulation utilities for LLVM codegen.

This module provides comprehensive helper functions for enum operations to
eliminate code duplication across the backend. Enums in Sushi are represented
as LLVM structs with two fields:
  - Field 0: i32 discriminant tag (identifies the variant)
  - Field 1: [N x i8] data array (stores associated variant data)

These utilities abstract the repetitive patterns of:
  - Extracting/comparing tags
  - Packing/unpacking variant data
  - Constructing enum values
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Union, List, Tuple

from llvmlite import ir

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.typesys import EnumType, EnumVariantInfo, Type


def extract_enum_tag(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    name: str = "tag"
) -> ir.Value:
    """Extract the discriminant tag (field 0) from an enum value.

    Args:
        codegen: LLVM code generator instance
        enum_value: The enum value (struct with {i32, [N x i8]} layout)
        name: Optional name for the extracted tag value

    Returns:
        i32 tag value identifying the enum variant

    Example:
        tag = extract_enum_tag(codegen, result_enum, "result_tag")
    """
    return codegen.builder.extract_value(enum_value, 0, name=name)


def extract_enum_data(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    name: str = "data"
) -> ir.Value:
    """Extract the data array (field 1) from an enum value.

    Args:
        codegen: LLVM code generator instance
        enum_value: The enum value (struct with {i32, [N x i8]} layout)
        name: Optional name for the extracted data array

    Returns:
        [N x i8] array containing the variant's associated data

    Example:
        data = extract_enum_data(codegen, result_enum, "ok_data")
    """
    return codegen.builder.extract_value(enum_value, 1, name=name)


def compare_enum_tags(
    codegen: 'LLVMCodegen',
    tag1: ir.Value,
    tag2: ir.Value,
    signed: bool = True,
    name: str = "tags_equal"
) -> ir.Value:
    """Compare two enum tags for equality.

    Args:
        codegen: LLVM code generator instance
        tag1: First i32 tag value
        tag2: Second i32 tag value
        signed: Use signed comparison (default True)
        name: Optional name for the comparison result

    Returns:
        i1 boolean indicating whether tags are equal

    Example:
        match = compare_enum_tags(codegen, actual_tag, expected_tag, name="variant_match")
    """
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
    """Check if an enum value matches a specific variant by index.

    This is a convenience function that combines tag extraction and comparison.

    Args:
        codegen: LLVM code generator instance
        enum_value: The enum value to check
        variant_index: The variant index to compare against (0-based)
        signed: Use signed comparison (default True)
        name: Optional name for the comparison result

    Returns:
        i1 boolean indicating whether the enum is the specified variant

    Example:
        is_ok = check_enum_variant(codegen, result_enum, 0, name="is_ok")
        is_err = check_enum_variant(codegen, result_enum, 1, name="is_err")
    """
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
    """Construct an enum value with a specific variant tag and optional data.

    Args:
        codegen: LLVM code generator instance
        enum_llvm_type: The LLVM struct type for the enum
        variant_index: The variant index (0-based)
        data: Optional [N x i8] array containing variant data (if None, data field is undefined)
        name_prefix: Prefix for generated value names

    Returns:
        Fully constructed enum value {i32, [N x i8]}

    Example:
        # Simple variant with no data
        none_enum = construct_enum_variant(codegen, maybe_type, 0, name_prefix="Maybe_None")

        # Variant with associated data
        some_data = pack_data(...)  # [N x i8] array
        some_enum = construct_enum_variant(codegen, maybe_type, 1, some_data, "Maybe_Some")
    """
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
    """Set the data field (field 1) of an enum value.

    This is useful when you've already created an enum with a tag and need
    to add the associated data afterward.

    Args:
        codegen: LLVM code generator instance
        enum_value: The enum value to modify
        data: The [N x i8] data array to insert
        name: Optional name for the result

    Returns:
        Modified enum value with data field set

    Example:
        enum_val = construct_enum_variant(codegen, result_type, 0)  # Tag only
        packed_data = pack_data(...)
        enum_val = set_enum_data(codegen, enum_val, packed_data, "Result_Ok")
    """
    return codegen.builder.insert_value(enum_value, data, 1, name=name)


def compare_tag_to_const(
    codegen: 'LLVMCodegen',
    tag: ir.Value,
    const_value: int,
    signed: bool = True,
    name: str = "tag_matches"
) -> ir.Value:
    """Compare an enum tag to a constant variant index.

    This is a convenience wrapper around compare_enum_tags for the common
    case of comparing against a constant.

    Args:
        codegen: LLVM code generator instance
        tag: The i32 tag value to compare
        const_value: The constant variant index to compare against
        signed: Use signed comparison (default True)
        name: Optional name for the comparison result

    Returns:
        i1 boolean indicating whether tag matches the constant

    Example:
        tag = extract_enum_tag(codegen, result_enum)
        is_ok = compare_tag_to_const(codegen, tag, 0, name="is_ok")
    """
    expected_tag = ir.Constant(codegen.types.i32, const_value)
    return compare_enum_tags(codegen, tag, expected_tag, signed=signed, name=name)


def unpack_variant_field(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_type: 'Type',
    offset: int,
    name: str = "field"
) -> Tuple[ir.Value, int]:
    """Unpack a single field from enum variant data at a given offset.

    Consolidates the common pattern of offset calculation + bitcast + load
    that appears in pattern matching, enum methods, and hashing.

    Args:
        codegen: LLVM code generator instance
        data_ptr: Pointer to the start of variant data (i8*)
        field_type: Semantic type of the field
        offset: Byte offset within the data array
        name: Optional name for the loaded value

    Returns:
        Tuple of (loaded_value, next_offset) where next_offset is
        offset + sizeof(field_type)

    Example:
        data_ptr = get_data_ptr(enum_value)
        value1, offset = unpack_variant_field(codegen, data_ptr, int32_type, 0, "field1")
        value2, offset = unpack_variant_field(codegen, data_ptr, string_type, offset, "field2")
    """
    field_llvm_type = codegen.types.ll_type(field_type)
    field_size = codegen.types.get_type_size_bytes(field_type)

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

    value = codegen.builder.load(typed_ptr, name=name)
    return value, offset + field_size


def unpack_all_variant_fields(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_types: List['Type'],
    name_prefix: str = "field"
) -> List[ir.Value]:
    """Unpack all fields from enum variant data.

    Iterates through all field types at their proper offsets and returns
    the loaded values in order.

    Args:
        codegen: LLVM code generator instance
        data_ptr: Pointer to the start of variant data (i8*)
        field_types: List of semantic types for each field
        name_prefix: Prefix for field names

    Returns:
        List of loaded field values in order

    Example:
        # For a variant with (i32, string) associated types:
        data_ptr = get_data_ptr(enum_value)
        values = unpack_all_variant_fields(codegen, data_ptr, [int32_type, string_type])
        # values[0] is i32, values[1] is string
    """
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
    """Pack a single field into enum variant data at a given offset.

    Consolidates the common pattern of offset calculation + bitcast + store
    that appears in enum constructors.

    Args:
        codegen: LLVM code generator instance
        data_ptr: Pointer to the start of variant data (i8*)
        field_value: Value to store
        field_type: Semantic type of the field
        offset: Byte offset within the data array
        name: Optional name for GEP operations

    Returns:
        Next offset (offset + sizeof(field_type))

    Example:
        data_ptr = get_data_ptr(enum_value)
        offset = pack_variant_field(codegen, data_ptr, value1, int32_type, 0, "field1")
        offset = pack_variant_field(codegen, data_ptr, value2, string_type, offset, "field2")
    """
    field_llvm_type = codegen.types.ll_type(field_type)
    field_size = codegen.types.get_type_size_bytes(field_type)

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

    codegen.builder.store(field_value, typed_ptr)
    return offset + field_size


def pack_all_variant_fields(
    codegen: 'LLVMCodegen',
    data_ptr: ir.Value,
    field_values: List[ir.Value],
    field_types: List['Type'],
    name_prefix: str = "field"
) -> None:
    """Pack all fields into enum variant data.

    Iterates through all field values and stores them at their proper offsets.

    Args:
        codegen: LLVM code generator instance
        data_ptr: Pointer to the start of variant data (i8*)
        field_values: List of values to store
        field_types: List of semantic types for each field
        name_prefix: Prefix for field names

    Example:
        # For a variant with (i32, string) associated types:
        data_ptr = get_data_ptr(enum_value)
        pack_all_variant_fields(codegen, data_ptr, [int_val, str_val], [int32_type, string_type])
    """
    if len(field_values) != len(field_types):
        raise ValueError(f"Mismatch: {len(field_values)} values vs {len(field_types)} types")

    offset = 0
    for i, (value, field_type) in enumerate(zip(field_values, field_types)):
        offset = pack_variant_field(
            codegen, data_ptr, value, field_type, offset,
            name=f"{name_prefix}_{i}"
        )


def get_data_ptr(
    codegen: 'LLVMCodegen',
    enum_ptr: ir.Value,
    name: str = "data_ptr"
) -> ir.Value:
    """Get pointer to the data array of an enum stored in memory.

    This is useful when you have a pointer to an enum value (not the value
    itself) and need to pack/unpack variant data.

    Args:
        codegen: LLVM code generator instance
        enum_ptr: Pointer to enum value in memory
        name: Optional name for the result pointer

    Returns:
        Pointer to the data array (i8*)

    Example:
        enum_ptr = alloca_enum_type
        data_ptr = get_data_ptr(codegen, enum_ptr)
        pack_all_variant_fields(codegen, data_ptr, values, types)
    """
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
