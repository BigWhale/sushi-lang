"""
Enum tag operation utilities.

This module provides helper functions for common enum operations to eliminate
code duplication across the backend. Enums in Sushi are represented as LLVM
structs with two fields:
  - Field 0: i32 discriminant tag (identifies the variant)
  - Field 1: [N x i8] data array (stores associated variant data)

These utilities abstract the repetitive patterns of extracting tags, comparing
variants, and constructing enum values.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Union

from llvmlite import ir

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


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
