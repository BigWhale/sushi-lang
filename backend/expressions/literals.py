"""
Literal expression emission for the Sushi language compiler.

This module handles emission of all literal types: integers, floats, booleans,
strings, blanks, and interpolated strings.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from semantics.ast import (
    Expr, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString
)
from internals.errors import raise_internal_error

if TYPE_CHECKING:
    from backend.codegen_llvm import LLVMCodegen


def emit_literal(codegen: 'LLVMCodegen', expr: Expr, to_i1: bool) -> ir.Value:
    """Dispatch literal emission to appropriate handler.

    Args:
        codegen: The LLVM codegen instance.
        expr: The literal expression.
        to_i1: Whether to convert result to i1 for boolean contexts.

    Returns:
        The LLVM value representing the literal.

    Raises:
        TypeError: If expression is not a literal type.
    """
    match expr:
        case IntLit():
            return emit_int_literal(codegen, expr)
        case FloatLit():
            return emit_float_literal(codegen, expr)
        case BoolLit():
            return emit_bool_literal(codegen, expr, to_i1)
        case BlankLit():
            return emit_blank_literal(codegen, expr)
        case StringLit():
            return emit_string_literal(codegen, expr)
        case InterpolatedString():
            return emit_interpolated_string(codegen, expr)
        case _:
            raise_internal_error("CE0099", type=type(expr).__name__)


def emit_int_literal(codegen: 'LLVMCodegen', expr: IntLit) -> ir.Value:
    """Emit integer literal as i32 constant.

    Args:
        codegen: The LLVM codegen instance.
        expr: The integer literal expression.

    Returns:
        An i32 constant representing the integer value.
    """
    return ir.Constant(codegen.types.i32, int(expr.value) & 0xFFFFFFFF)


def emit_float_literal(codegen: 'LLVMCodegen', expr: FloatLit) -> ir.Value:
    """Emit floating-point literal as f64 constant.

    Args:
        codegen: The LLVM codegen instance.
        expr: The floating-point literal expression.

    Returns:
        An f64 constant representing the floating-point value.
    """
    return ir.Constant(codegen.types.f64, float(expr.value))


def emit_bool_literal(codegen: 'LLVMCodegen', expr: BoolLit, to_i1: bool) -> ir.Value:
    """Emit boolean literal with appropriate width.

    Args:
        codegen: The LLVM codegen instance.
        expr: The boolean literal expression.
        to_i1: Whether to emit as i1 or i8.

    Returns:
        An i1 or i8 constant representing the boolean value.
    """
    if to_i1:
        return ir.Constant(codegen.i1, 1 if expr.value else 0)
    else:
        return ir.Constant(codegen.i8, 1 if expr.value else 0)


def emit_blank_literal(codegen: 'LLVMCodegen', expr: BlankLit) -> ir.Value:
    """Emit blank literal as i32 zero constant.

    Args:
        codegen: The LLVM codegen instance.
        expr: The blank literal expression.

    Returns:
        An i32 constant representing the blank value (always 0).
    """
    return ir.Constant(codegen.types.i32, 0)


def emit_string_literal(codegen: 'LLVMCodegen', expr: StringLit) -> ir.Value:
    """Emit string literal using runtime support.

    Args:
        codegen: The LLVM codegen instance.
        expr: The string literal expression.

    Returns:
        An i8* pointer to the string constant.
    """
    return codegen.runtime.strings.emit_string_literal(expr.value)


def emit_interpolated_string(codegen: 'LLVMCodegen', expr: InterpolatedString) -> ir.Value:
    """Emit LLVM IR for interpolated string by concatenating string parts and expression values.

    For "Hello, {name}!" we emit:
    1. string_literal("Hello, ")
    2. emit_expression(name).to_str()
    3. string_literal("!")
    4. concatenate all parts

    Args:
        codegen: The LLVM codegen instance.
        expr: The interpolated string expression.

    Returns:
        The concatenated string value.
    """
    if not expr.parts:
        # Empty interpolated string - return empty string literal
        return codegen.runtime.strings.emit_string_literal("")

    # Handle single string literal case (no interpolation)
    if len(expr.parts) == 1 and isinstance(expr.parts[0], str):
        return codegen.runtime.strings.emit_string_literal(expr.parts[0])

    # Build list of string values to concatenate
    string_values = []

    for part in expr.parts:
        if isinstance(part, str):
            # String literal part - emit as string literal
            string_values.append(codegen.runtime.strings.emit_string_literal(part))
        else:
            # Expression part - emit expression and convert to string if needed
            # Use codegen.expressions for recursive call
            expr_value = codegen.expressions.emit_expr(part)

            # Check if the expression is already a string (fat pointer struct)
            if codegen.types.is_string_type(expr_value.type):
                # Already a string fat pointer, use it directly
                string_values.append(expr_value)
            else:
                # Not a string, need to convert using appropriate to_str implementation
                # Directly call the conversion functions based on LLVM type
                llvm_type = expr_value.type

                if isinstance(llvm_type, ir.IntType):
                    # Integer type - determine signedness and width
                    width = llvm_type.width
                    if width == 1:
                        # bool (i1)
                        string_values.append(codegen.runtime.formatting.emit_bool_to_string(expr_value))
                    elif width in [8, 16, 32, 64]:
                        # Determine signedness from the expression's type
                        is_signed = True  # default to signed

                        # Try multiple methods to determine signedness
                        # Method 1: Check inferred_return_type attribute (set by type checker)
                        if hasattr(part, 'inferred_return_type'):
                            from semantics.typesys import BuiltinType
                            inferred_type = part.inferred_return_type
                            if inferred_type in [BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64]:
                                is_signed = False
                        # Method 2: For Name nodes, look up variable type
                        elif hasattr(part, 'id'):  # Name node
                            from semantics.ast import Name
                            from semantics.typesys import BuiltinType
                            if isinstance(part, Name) and part.id in codegen.variable_types:
                                var_type = codegen.variable_types[part.id]
                                if var_type in [BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64]:
                                    is_signed = False

                        string_values.append(codegen.runtime.formatting.emit_integer_to_string(expr_value, is_signed=is_signed, bit_width=width))
                    else:
                        raise_internal_error("CE0022", type=f"i{{width}}")
                elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
                    # Float type
                    is_double = isinstance(llvm_type, ir.DoubleType)
                    string_values.append(codegen.runtime.formatting.emit_float_to_string(expr_value, is_double=is_double))
                else:
                    raise_internal_error("CE0022", type=str(llvm_type))

    # If we have only one string value, return it directly
    if len(string_values) == 1:
        return string_values[0]

    # Concatenate all string values using runtime string concatenation
    result = string_values[0]
    for string_value in string_values[1:]:
        # Use the runtime string concatenation function
        result = codegen.runtime.strings.emit_string_concat(result, string_value)

    return result
