"""
Result<T, E> Err-value construction for the LLVM backend.

Error propagation (`??`) is the one place the backend has to synthesize a Result value
out of nothing: it must build the enclosing function's Err variant to return on the
error path. That is this module's whole job.

Type *registration* is not done here -- `semantics.generics.results.ensure_result_type_in_table`
owns it, and this module calls it. (There used to be a second, byte-for-byte copy of that
registration behind a per-call instance cache that could never hit.)
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

import llvmlite.ir as ir

from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def intern_result(codegen: 'LLVMCodegen', ok_type: Type, err_type: Type) -> Optional[EnumType]:
    """The interned ``Result<ok, err>`` enum, using this codegen's tables.

    The backend's single way of naming a Result. It exists so no site is tempted to build the
    type structurally again: ``ensure_result_type_in_table`` resolves the payloads before it
    mangles the name, and skipping that is what poisons the enum table (CE0126).
    """
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    return ensure_result_type_in_table(
        codegen.enum_table, ok_type, err_type,
        struct_table=codegen.struct_table.by_name,
    )


def implicit_result_of(codegen: 'LLVMCodegen', fn) -> Optional[EnumType]:
    """The interned Result a function's declared return type implies.

    `fn foo() T` means `Result<T, StdError>`; `fn foo() T | E` means `Result<T, E>`. Pass 2 has
    already worked this out, but the backend re-derives it in a handful of places (prototype
    emission, the main wrapper, the `??` error path).
    """
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    err_type = getattr(fn, 'err_type', None)
    if err_type is not None:
        err_type = resolve_unknown_type(
            err_type, codegen.struct_table.by_name, codegen.enum_table.by_name)
    else:
        err_type = codegen.enum_table.by_name.get("StdError")
    if err_type is None:
        err_type = fn.ret  # Fallback (shouldn't happen with StdError registered)
    return intern_result(codegen, fn.ret, err_type)


def build_err_from_return_type(
    codegen: 'LLVMCodegen',
    return_type: Type,
    error_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct the Err variant of a function's Result return type.

    Accepts the interned Result<T, E> EnumType (what Pass 2 stamps) or a
    GenericTypeRef("Result", [T, E]).

    Args:
        codegen: The LLVM code generator instance.
        return_type: The enclosing function's return type.
        error_value: The LLVM value for the error payload (optional).

    Returns:
        LLVM value representing Result.Err(error).
    """
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum, result_ok_err,
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if is_result_enum(return_type):
        return _build_err_variant(codegen, return_type, error_value)

    if isinstance(return_type, GenericTypeRef) and return_type.base_name == "Result":
        if len(return_type.type_args) != 2:
            raise_internal_error("CE0040", variant="Err",
                type=f"Result must have exactly 2 type parameters, got {len(return_type.type_args)}")
        ok_type, err_type = return_type.type_args[0], return_type.type_args[1]
    else:
        raise_internal_error("CE0040", variant="Err",
            type=f"Expected Result<T, E>, got {return_type}")

    enum_type = ensure_result_type_in_table(codegen.enum_table, ok_type, err_type, struct_table=codegen.struct_table.by_name)
    if enum_type is None:
        raise_internal_error("CE0091", type=str(return_type))

    return _build_err_variant(codegen, enum_type, error_value)


def _build_err_variant(
    codegen: 'LLVMCodegen',
    result_type: EnumType,
    error_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct a Result.Err(error) LLVM value for a concrete Result enum."""
    from sushi_lang.backend import enum_utils

    err_tag = result_type.get_variant_index("Err")
    if err_tag is None:
        raise_internal_error("CE0035", variant="Err", enum=result_type.name)

    enum_llvm_type = codegen.types.ll_type(result_type)

    enum_value = enum_utils.construct_enum_variant(
        codegen, enum_llvm_type, err_tag,
        data=None, name_prefix=f"{result_type.name}_Err"
    )

    if error_value is not None:
        data_array_type = enum_llvm_type.elements[1]
        temp_alloca = codegen.builder.alloca(data_array_type, name="err_data_temp")
        data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="err_data_ptr")

        error_ptr_typed = codegen.builder.bitcast(
            data_ptr, ir.PointerType(error_value.type), name="err_ptr_typed"
        )
        codegen.builder.store(error_value, error_ptr_typed, align=1)  # under-aligned enum payload (#145)

        packed_data = codegen.builder.load(temp_alloca, name="packed_err_data")
        enum_value = enum_utils.set_enum_data(
            codegen, enum_value, packed_data,
            name=f"{result_type.name}_Err_data"
        )

    return enum_value
