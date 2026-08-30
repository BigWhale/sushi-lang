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

    Emitted in a string-temp frame of its OWN: every buffer built here is this
    interpolation's, freed as the next concat copies its bytes, and the result belongs to
    the position it lands in -- a `let`, a call argument's owner, or the print frame that
    registers the whole value (#521).
    """
    with codegen.string_temps_own_frame():
        return _emit_interpolated_string(codegen, expr)


def _emit_interpolated_string(codegen: 'LLVMCodegen', expr: InterpolatedString) -> ir.Value:
    """Build the concatenation. See `emit_interpolated_string` for the ownership rule."""
    if not expr.parts:
        return codegen.runtime.strings.emit_string_literal("")

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
            string_values.append(codegen.runtime.strings.emit_string_literal(part))
            fresh_flags.append(False)
        else:
            expr_value = codegen.expressions.emit_expr(part)

            if codegen.types.is_string_type(expr_value.type):
                # A string part with a live owner is a BORROW -- never free it here. A
                # TEMPORARY part is owned by nobody, so the concat loop below frees it,
                # and it is registered in THIS interpolation's frame so an early exit out
                # of a later part frees it too (#295).
                from sushi_lang.backend.expressions.memory import expression_is_temporary
                fresh = expression_is_temporary(codegen, part)
                if fresh:
                    codegen.register_string_value_temp(expr_value)
                fresh_flags.append(fresh)
                string_values.append(expr_value)
            else:
                llvm_type = expr_value.type

                if isinstance(llvm_type, ir.IntType):
                    width = llvm_type.width
                    if width == 1:
                        string_values.append(codegen.runtime.formatting.emit_bool_to_string(expr_value))
                        fresh_flags.append(True)
                    elif width in [8, 16, 32, 64]:
                        from sushi_lang.semantics.typesys import BuiltinType
                        from sushi_lang.backend.expressions.type_utils import (
                            infer_expr_semantic_type, is_unsigned_type,
                        )
                        # A bool lowers to i8 in most positions, so it reaches this
                        # width ladder. The SEMANTIC type decides the rendering: a bool
                        # hole prints true/false whatever the expression's shape (#514).
                        part_type = infer_expr_semantic_type(codegen, part)
                        if (part_type == BuiltinType.BOOL
                                or getattr(part, 'inferred_return_type', None)
                                == BuiltinType.BOOL):
                            string_values.append(codegen.runtime.formatting.emit_bool_to_string(expr_value))
                            fresh_flags.append(True)
                            continue

                        # Signedness from the same source the print statements use; it
                        # reads the typecheck pass's stamp before falling back to its
                        # own reconstruction.
                        is_signed = not is_unsigned_type(part_type)
                        string_values.append(codegen.runtime.formatting.emit_integer_to_string(expr_value, is_signed=is_signed, bit_width=width))
                        fresh_flags.append(True)
                    else:
                        raise_internal_error("CE0022", type=f"i{width}")
                elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
                    is_double = isinstance(llvm_type, ir.DoubleType)
                    string_values.append(codegen.runtime.formatting.emit_float_to_string(expr_value, is_double=is_double))
                    fresh_flags.append(True)
                else:
                    raise_internal_error("CE0022", type=str(llvm_type))

    if len(string_values) == 1:
        return string_values[0]

    # Each consumed FRESH intermediate is freed once the concat has copied its bytes
    # (#145); a literal or borrowed part is never freed, and the RESULT goes to its new
    # owner unfreed. Unconditional: an interpolation owns what it built wherever it is
    # emitted, and the position the result lands in owns the result (#521).
    from sushi_lang.backend.destructors import emit_string_destructor_from_value

    result = string_values[0]
    result_fresh = fresh_flags[0]
    for string_value, sv_fresh in zip(string_values[1:], fresh_flags[1:], strict=True):
        new_result = codegen.runtime.strings.emit_string_concat(result, string_value)
        if result_fresh:
            emit_string_destructor_from_value(codegen, result)
        if sv_fresh:
            emit_string_destructor_from_value(codegen, string_value)
        result = new_result
        result_fresh = True  # a concat output is always a fresh heap buffer

    return result
