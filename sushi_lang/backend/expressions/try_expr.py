"""Error-propagation (`??`) emission for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import TryExpr
    from sushi_lang.semantics.typesys import Type


def emit_try_expr(codegen: 'LLVMCodegen', expr: 'TryExpr') -> ir.Value:
    """Emit try operator (??) for error propagation with Result<T> or Maybe<T>."""
    inner_type = expr.inferred_inner_type
    unwrapped_type = expr.inferred_unwrapped_type
    success_tag = expr.inferred_success_tag
    error_type = expr.inferred_error_type
    func_return_type = expr.inferred_func_return_type

    if inner_type is None or unwrapped_type is None or success_tag is None:
        raise_internal_error("CE0124")

    result_value = codegen.expressions.emit_expr(expr.expr)

    is_success = enum_utils.check_enum_variant(
        codegen, result_value, success_tag, signed=True, name="is_success"
    )

    unwrapped_value = _extract_variant_from_result(codegen, result_value, unwrapped_type)

    error_value = None
    if error_type is not None:
        error_value = _extract_variant_from_result(codegen, result_value, error_type)

    propagate_block = codegen.func.append_basic_block(name="try_propagate_err")
    continue_block = codegen.func.append_basic_block(name="try_continue")

    codegen.builder.cbranch(is_success, continue_block, propagate_block)

    codegen.builder.position_at_end(propagate_block)

    from sushi_lang.backend.statements import utils
    utils.emit_scope_cleanup(codegen, cleanup_type='all')

    err_result = _construct_result_err_variant(codegen, func_return_type, error_value)
    codegen.builder.ret(err_result)

    codegen.builder.position_at_end(continue_block)
    return unwrapped_value


def _extract_variant_from_result(codegen: 'LLVMCodegen', result_value: ir.Value, variant_type: 'Type') -> ir.Value:
    """Extract variant data from Result/Maybe enum value."""
    variant_llvm_type = codegen.types.ll_type(variant_type)
    _, extracted_value = codegen.functions._extract_value_from_result_enum(
        result_value,
        variant_llvm_type,
        variant_type
    )
    return extracted_value


def _construct_result_err_variant(codegen: 'LLVMCodegen', return_type, error_value: ir.Value) -> ir.Value:
    """Construct an Err variant enum value for Result<T, E> with actual error data."""
    from sushi_lang.backend.generics.result_builder import build_err_from_return_type
    return build_err_from_return_type(codegen, return_type, error_value)

