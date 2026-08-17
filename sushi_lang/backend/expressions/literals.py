"""Literal expression emission for the Sushi language compiler."""
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
    """Dispatch literal emission to appropriate handler."""
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
    """Emit an integer literal at its context type's width (default i32)."""
    ty = expr.resolved_type or BuiltinType.I32
    ll = codegen.types.ll_type(ty)
    mask = (1 << ll.width) - 1
    return ir.Constant(ll, int(expr.value) & mask)


def emit_float_literal(codegen: 'LLVMCodegen', expr: FloatLit) -> ir.Value:
    """Emit a float literal at its context type's width (default f64)."""
    ty = expr.resolved_type or BuiltinType.F64
    return ir.Constant(codegen.types.ll_type(ty), float(expr.value))


def emit_bool_literal(codegen: 'LLVMCodegen', expr: BoolLit, to_i1: bool) -> ir.Value:
    """Emit boolean literal with appropriate width."""
    if to_i1:
        return ir.Constant(codegen.i1, 1 if expr.value else 0)
    else:
        return ir.Constant(codegen.i8, 1 if expr.value else 0)


def emit_blank_literal(codegen: 'LLVMCodegen', expr: BlankLit) -> ir.Value:
    """Emit blank literal as i32 zero constant."""
    return ir.Constant(codegen.types.i32, 0)


def emit_string_literal(codegen: 'LLVMCodegen', expr: StringLit) -> ir.Value:
    """Emit string literal using runtime support."""
    return codegen.runtime.strings.emit_string_literal(expr.value)


def emit_interpolated_string(codegen: 'LLVMCodegen', expr: InterpolatedString) -> ir.Value:
    """Emit LLVM IR for interpolated string by concatenating string parts and expression values.
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
                # A string-typed part is a BORROW when a live owner frees it (`{name}`, a
                # field read, a container get-out) -- use directly, never free here. A
                # TEMPORARY string part (`{s.upper()}`, a call result, a `??` unwrap) is an
                # owned value nobody else frees: inside a print-arg frame the
                # frame frees it after output (the concat loop below is disabled there);
                # outside one the concat loop frees it like any other fresh intermediate.
                from sushi_lang.backend.expressions.memory import expression_is_temporary
                if expression_is_temporary(codegen, part):
                    if codegen._string_temp_stack:
                        codegen.register_string_value_temp(expr_value)
                        fresh_flags.append(False)
                    else:
                        fresh_flags.append(True)
                else:
                    fresh_flags.append(False)
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
                        fresh_flags.append(True)
                    elif width in [8, 16, 32, 64]:
                        from sushi_lang.semantics.typesys import BuiltinType
                        from sushi_lang.backend.expressions.type_utils import (
                            infer_expr_semantic_type, is_unsigned_type,
                        )
                        # bool-returning methods (contains/starts_with/ends_with)
                        # lower to i8, not i1, so they fall through to the integer
                        # path; format them as true/false. Gated on the type
                        # checker's stamp so a plain bool value keeps its historical
                        # 1/0 rendering.
                        inferred = getattr(part, 'inferred_return_type', None)
                        if inferred == BuiltinType.BOOL:
                            string_values.append(codegen.runtime.formatting.emit_bool_to_string(expr_value))
                            fresh_flags.append(True)
                            continue

                        # Signedness from the part's semantic type: prefer the
                        # checker's stamp, else reconstruct it (handles casts,
                        # consts, and locals) - the source the print statements use.
                        part_type = inferred if inferred is not None else infer_expr_semantic_type(codegen, part)
                        is_signed = not is_unsigned_type(part_type)
                        string_values.append(codegen.runtime.formatting.emit_integer_to_string(expr_value, is_signed=is_signed, bit_width=width))
                        fresh_flags.append(True)
                    else:
                        raise_internal_error("CE0022", type=f"i{width}")
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
    for string_value, sv_fresh in zip(string_values[1:], fresh_flags[1:], strict=True):
        new_result = codegen.runtime.strings.emit_string_concat(result, string_value)
        if free_intermediates:
            if result_fresh:
                emit_string_destructor_from_value(codegen, result)
            if sv_fresh:
                emit_string_destructor_from_value(codegen, string_value)
        result = new_result
        result_fresh = True  # a concat output is always a fresh heap buffer

    return result
