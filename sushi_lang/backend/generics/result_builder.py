"""Result<T, E> Err-value construction for the LLVM backend."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

import llvmlite.ir as ir

from sushi_lang.semantics.typesys import EnumType, Type
from sushi_lang.internals.errors import raise_internal_error

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


def intern_result(codegen: 'LLVMCodegen', ok_type: Type, err_type: Type) -> Optional[EnumType]:
    """The interned ``Result<ok, err>`` enum, using this codegen's tables."""
    from sushi_lang.semantics.generics.results import ensure_result_type_in_table
    return ensure_result_type_in_table(
        codegen.enum_table, ok_type, err_type,
        struct_table=codegen.struct_table.by_name,
    )


def implicit_result_of(codegen: 'LLVMCodegen', fn) -> Optional[EnumType]:
    """The interned Result a function's declared return type implies."""
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


def extension_result_of(codegen: 'LLVMCodegen', ext) -> Optional[EnumType]:
    """The interned Result a CHANNEL extension ('| E', ruling 1) returns.

    None for a bare extension -- the one reader for "does this ExtendDef have the
    Result ABI", used by the declaration, the body emission and the default return.
    """
    err_type = getattr(ext, 'err_type', None)
    if err_type is None:
        return None
    from sushi_lang.semantics.type_resolution import resolve_unknown_type
    err_resolved = resolve_unknown_type(
        err_type, codegen.struct_table.by_name, codegen.enum_table.by_name)
    return intern_result(codegen, ext.ret, err_resolved)


def build_ok_variant(
    codegen: 'LLVMCodegen',
    result_type: EnumType,
    ok_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct a Result.Ok(value) LLVM value for a concrete Result enum.

    This is the emission seam of ruling 6: a channel extension's bare success return
    wraps here, so the body never spells the constructor.
    """
    return _build_payload_variant(codegen, result_type, "Ok", ok_value)


def build_err_from_return_type(
    codegen: 'LLVMCodegen',
    return_type: Type,
    error_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct the Err variant of a function's Result return type."""
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum,
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
    return _build_payload_variant(codegen, result_type, "Err", error_value)


def _build_payload_variant(
    codegen: 'LLVMCodegen',
    result_type: EnumType,
    variant: str,
    payload_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct one variant of a concrete Result enum, with an optional payload."""
    from sushi_lang.backend import enum_utils

    tag = result_type.get_variant_index(variant)
    if tag is None:
        raise_internal_error("CE0035", variant=variant, enum=result_type.name)

    enum_llvm_type = codegen.types.ll_type(result_type)

    enum_value = enum_utils.construct_enum_variant(
        codegen, enum_llvm_type, tag,
        data=None, name_prefix=f"{result_type.name}_{variant}"
    )

    if payload_value is not None:
        data_array_type = enum_llvm_type.elements[1]
        # ENTRY block: a variant built inside a loop must not grow the frame (BUGS.md B1).
        temp_alloca = codegen.memory.entry_alloca(data_array_type, f"{variant.lower()}_data_temp")
        data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr,
                                           name=f"{variant.lower()}_data_ptr")

        payload_ptr_typed = codegen.builder.bitcast(
            data_ptr, ir.PointerType(payload_value.type), name=f"{variant.lower()}_ptr_typed"
        )
        # Natural alignment: the data member is a [K x i64] array (#300 phase 2).
        codegen.builder.store(payload_value, payload_ptr_typed)

        packed_data = codegen.builder.load(temp_alloca, name=f"packed_{variant.lower()}_data")
        enum_value = enum_utils.set_enum_data(
            codegen, enum_value, packed_data,
            name=f"{result_type.name}_{variant}_data"
        )

    return enum_value
