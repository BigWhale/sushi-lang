"""
Literal expression emission for the Sushi language compiler.

This module handles emission of all literal types: integers, floats, booleans,
strings, blanks, and interpolated strings.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.semantics.ast import (
    Expr, IntLit, FloatLit, BoolLit, BlankLit, StringLit, InterpolatedString
)
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


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
    """Emit an integer literal at its context type's width (default i32).

    A context-typed literal (`resolved_type` stamped by propagation) materializes at
    the annotated width, masked to that width -- identical IR to the equivalent
    `<lit> as T` cast path. An unstamped literal keeps the i32 default.
    """
    ty = expr.resolved_type or BuiltinType.I32
    ll = codegen.types.ll_type(ty)
    mask = (1 << ll.width) - 1
    return ir.Constant(ll, int(expr.value) & mask)


def emit_float_literal(codegen: 'LLVMCodegen', expr: FloatLit) -> ir.Value:
    """Emit a float literal at its context type's width (default f64).

    A context-typed literal (`resolved_type` stamped by propagation) materializes as
    f32/f64 as annotated; an unstamped literal keeps the f64 default.
    """
    ty = expr.resolved_type or BuiltinType.F64
    return ir.Constant(codegen.types.ll_type(ty), float(expr.value))


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

    # Build list of (value, is_fresh) to concatenate. `is_fresh` marks a heap temporary this
    # interpolation OWNS and may free after it is consumed (a to-string / concat buffer);
    # a literal (owned=0) or an existing string variable (a BORROW aliasing another owner)
    # is NOT fresh and must never be freed here (#145).
    string_values = []
    fresh_flags = []

    for part in expr.parts:
        if isinstance(part, str):
            # String literal part - emit as string literal (owned=0, borrow-like: not fresh)
            string_values.append(codegen.runtime.strings.emit_string_literal(part))
            fresh_flags.append(False)
        else:
            # Expression part - emit expression and convert to string if needed
            # Use codegen.expressions for recursive call
            expr_value = codegen.expressions.emit_expr(part)

            # Check if the expression is already a string (fat pointer struct)
            if codegen.types.is_string_type(expr_value.type):
                # Already a string fat pointer -- a BORROW of its owner (e.g. `{name}`);
                # use directly and never free it here.
                string_values.append(expr_value)
                fresh_flags.append(False)
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
                        fresh_flags.append(True)
                    elif width in [8, 16, 32, 64]:
                        # Determine signedness from the expression's type
                        is_signed = True  # default to signed

                        # Try multiple methods to determine signedness
                        # Method 1: Check inferred_return_type attribute (set by type checker)
                        if hasattr(part, 'inferred_return_type'):
                            from sushi_lang.semantics.typesys import BuiltinType
                            inferred_type = part.inferred_return_type
                            # bool-returning methods (contains/starts_with/ends_with)
                            # lower to i8, not i1, so they fall through to the
                            # integer path; format them as true/false explicitly.
                            if inferred_type == BuiltinType.BOOL:
                                string_values.append(codegen.runtime.formatting.emit_bool_to_string(expr_value))
                                fresh_flags.append(True)
                                continue
                            if inferred_type in [BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64]:
                                is_signed = False
                        # Method 2: For Name nodes, look up variable type
                        elif hasattr(part, 'id'):  # Name node
                            from sushi_lang.semantics.ast import Name
                            from sushi_lang.semantics.typesys import BuiltinType
                            if isinstance(part, Name) and part.id in codegen.variable_types:
                                var_type = codegen.variable_types[part.id]
                                if var_type in [BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64]:
                                    is_signed = False

                        string_values.append(codegen.runtime.formatting.emit_integer_to_string(expr_value, is_signed=is_signed, bit_width=width))
                        fresh_flags.append(True)
                    else:
                        raise_internal_error("CE0022", type=f"i{{width}}")
                elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
                    # Float type
                    is_double = isinstance(llvm_type, ir.DoubleType)
                    string_values.append(codegen.runtime.formatting.emit_float_to_string(expr_value, is_double=is_double))
                    fresh_flags.append(True)
                else:
                    raise_internal_error("CE0022", type=str(llvm_type))

    # If we have only one string value, return it directly
    if len(string_values) == 1:
        return string_values[0]

    # Concatenate all string values, freeing each consumed FRESH intermediate right after the
    # concat copies its bytes (#145). A literal / borrowed variable part is not fresh and is
    # never freed. The final result is returned unfreed (its new owner -- a `let` local via the
    # scope registry, or the print statement -- frees it). Skip the freeing entirely inside a
    # print argument: the #141 print-temp registry already frees these buffers there, and doing
    # it here too would double-free.
    from sushi_lang.backend.destructors import emit_string_destructor_from_value
    free_intermediates = not codegen._string_temp_stack

    result = string_values[0]
    result_fresh = fresh_flags[0]
    for string_value, sv_fresh in zip(string_values[1:], fresh_flags[1:]):
        new_result = codegen.runtime.strings.emit_string_concat(result, string_value)
        if free_intermediates:
            if result_fresh:
                emit_string_destructor_from_value(codegen, codegen.builder, result)
            if sv_fresh:
                emit_string_destructor_from_value(codegen, codegen.builder, string_value)
        result = new_result
        result_fresh = True  # a concat output is always a fresh heap buffer

    return result
