"""
Unified generic enum method implementations for Result<T> and Maybe<T>.

This module provides common patterns shared between Result and Maybe:
- realise(default): Extract value with fallback
- Tag checking: is_ok/is_some, is_err/is_none
- Value extraction from enum data fields

These patterns were previously duplicated across results.py and maybe.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Tuple
import llvmlite.ir as ir

from backend.constants import INT32_BIT_WIDTH
from semantics.typesys import EnumType, Type
from internals.errors import raise_internal_error
from backend import enum_utils

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen
    from semantics.ast import MethodCall


def emit_enum_tag_check(
    codegen: 'LLVMCodegen',
    enum_value: ir.Value,
    expected_tag: int,
    check_name: str
) -> ir.Value:
    """Extract enum tag and compare to expected value.

    Generic implementation for is_ok(), is_some(), is_err(), is_none(), etc.

    Args:
        codegen: The LLVM code generator instance
        enum_value: The LLVM value of the enum (struct value, not pointer)
        expected_tag: The tag value to check against (e.g., 0 for Ok/Some)
        check_name: Name for the comparison result (e.g., "is_ok", "is_some")

    Returns:
        i1 value: true if tag matches expected_tag, false otherwise
    """
    # Extract tag and compare to expected value
    return enum_utils.check_enum_variant(
        codegen, enum_value, expected_tag, signed=False, name=check_name
    )


def emit_enum_realise(
    codegen: 'LLVMCodegen',
    call: 'MethodCall',
    enum_value: ir.Value,
    enum_type: EnumType,
    success_variant_name: str,
    enum_type_name: str
) -> ir.Value:
    """Emit LLVM code for enum.realise(default) pattern.

    Generic implementation that works for both Result<T>.realise() and Maybe<T>.realise().

    Enum layout: {i32 tag, [N x i8] data}
    - success_variant_name has tag = 0 (Ok for Result, Some for Maybe)
    - failure variant has tag = 1 (Err for Result, None for Maybe)

    Returns: tag == 0 ? unpacked_value : default

    Args:
        codegen: The LLVM code generator instance
        call: The method call AST node
        enum_value: The LLVM value of the enum
        enum_type: The enum type (Result<T> or Maybe<T>)
        success_variant_name: Name of the success variant ("Ok" or "Some")
        enum_type_name: Name for error messages ("Result" or "Maybe")

    Returns:
        The extracted value if success variant, or default if failure variant

    Raises:
        ValueError: If argument count is not exactly 1
        RuntimeError: If variant structure is invalid
        TypeError: If type mismatch occurs
    """
    if len(call.args) != 1:
        raise_internal_error("CE0023", method="realise", expected=1, got=len(call.args))

    # Extract T from generic enum
    success_variant = enum_type.get_variant(success_variant_name)
    if success_variant is None:
        raise_internal_error("CE0035", variant=success_variant_name, enum=enum_type.name)

    if len(success_variant.associated_types) != 1:
        raise_internal_error("CE0036", variant=success_variant_name, expected=1, got=len(success_variant.associated_types))

    t_type = success_variant.associated_types[0]

    # Get the LLVM type for T
    value_llvm_type = codegen.types.ll_type(t_type)

    # Extract (is_success, value) from enum using the helper from llvm_functions.py
    # This helper handles the complex unpacking of the enum's [N x i8] data field
    # Pass semantic type for accurate size calculation (critical for struct types)
    is_success, unpacked_value = codegen.functions._extract_value_from_result_enum(
        enum_value, value_llvm_type, t_type
    )

    # Emit the default value expression
    default_value = codegen.expressions.emit_expr(call.args[0])

    # Ensure default_value has the same LLVM type as unpacked_value
    # The LLVM select instruction requires both operands to have identical types
    if default_value.type != value_llvm_type:
        # Type mismatch - need to convert default_value to match
        # Handle float-to-float conversions (f32 <-> f64)
        if isinstance(default_value.type, (ir.FloatType, ir.DoubleType)) and isinstance(value_llvm_type, (ir.FloatType, ir.DoubleType)):
            if isinstance(value_llvm_type, ir.DoubleType) and isinstance(default_value.type, ir.FloatType):
                # f32 -> f64: extend precision
                default_value = codegen.builder.fpext(default_value, value_llvm_type)
            elif isinstance(value_llvm_type, ir.FloatType) and isinstance(default_value.type, ir.DoubleType):
                # f64 -> f32: truncate precision
                default_value = codegen.builder.fptrunc(default_value, value_llvm_type)
        # Handle integer-to-integer conversions (i8 <-> i32, i16 <-> i64, etc.)
        elif isinstance(default_value.type, ir.IntType) and isinstance(value_llvm_type, ir.IntType):
            src_width = default_value.type.width
            dst_width = value_llvm_type.width
            if src_width < dst_width:
                # Extend: i8 -> i32, i32 -> i64, etc.
                # Use sign extension for signed types (i32 is signed in Sushi)
                default_value = codegen.builder.sext(default_value, value_llvm_type)
            elif src_width > dst_width:
                # Truncate: i32 -> i8, i64 -> i32, etc.
                default_value = codegen.builder.trunc(default_value, value_llvm_type)
        else:
            # Type mismatch that shouldn't happen after proper semantic analysis
            raise_internal_error("CE0017", src=str(default_value.type), dst=str(value_llvm_type))

    # Select: is_success ? unpacked_value : default_value
    result = codegen.builder.select(is_success, unpacked_value, default_value, name="realise_result")

    return result
