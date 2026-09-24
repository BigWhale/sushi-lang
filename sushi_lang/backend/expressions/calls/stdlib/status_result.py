"""The Result of a stdlib call whose generated function answers a bare status value.

The contract: a NEGATIVE status is a failure and answers `Result.Err(<error variant>)`;
any other status is the success value. `<time>` and `<sys/env>` obey it.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from llvmlite import ir

from sushi_lang.backend.generics.result_builder import (
    build_err_from_return_type, build_ok_variant, intern_result,
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import Type

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def _interned_result(codegen: 'LLVMCodegen', ok_type: Type, error_enum: str):
    err_type = codegen.enum_table.by_name.get(error_enum)
    result_enum = intern_result(codegen, ok_type, err_type) if err_type is not None else None
    if err_type is None or result_enum is None:
        raise_internal_error("CE0091", type=f"Result@({ok_type}, {error_enum})")
    return result_enum, err_type


def ok_result(codegen: 'LLVMCodegen', value: ir.Value, ok_type: Type, error_enum: str) -> ir.Value:
    """`Result.Ok(value)` for a call that cannot fail."""
    result_enum, _ = _interned_result(codegen, ok_type, error_enum)
    return build_ok_variant(codegen, result_enum, value)


def status_result(codegen: 'LLVMCodegen', status: ir.Value, ok_type: Type,
                  error_enum: str, error_variant: str) -> ir.Value:
    """`Result.Err(error_enum.error_variant)` for a negative status, else `Result.Ok(status)`."""
    result_enum, err_type = _interned_result(codegen, ok_type, error_enum)
    tag = err_type.get_variant_index(error_variant)
    if tag is None:
        raise_internal_error("CE0035", variant=error_variant, enum=error_enum)

    err_llvm_type = codegen.types.ll_type(err_type)
    error_value = ir.Constant(err_llvm_type, [ir.Constant(codegen.types.i32, tag),
                                              ir.Constant(err_llvm_type.elements[1], None)])
    ok_value = build_ok_variant(codegen, result_enum, status)
    err_value = build_err_from_return_type(codegen, result_enum, error_value)
    failed = codegen.builder.icmp_signed('<', status, ir.Constant(status.type, 0), name="status_failed")
    return codegen.builder.select(failed, err_value, ok_value, name="status_result")
