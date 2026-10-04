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


def channel_result_of(codegen: 'LLVMCodegen', fn) -> Optional[EnumType]:
    """The interned Result a callable returns, or None for a BARE one.

    The one backend reader of "does this callable have the Result ABI", for a function,
    an extension or perk method and a library signature alike: it has one when it
    writes `| E` or returns an explicit `Result@(T, E)` (docs/design/error-channel.md).
    A spelled Result return arrives as the resolve pass's stamp (#857).
    """
    from sushi_lang.semantics.channel import declared_return, spells_result
    from sushi_lang.semantics.generics.results import is_result_enum, signature_result_arms
    from sushi_lang.semantics.type_resolution import resolve_unknown_type

    from sushi_lang.semantics.channel import has_channel
    if not has_channel(fn):
        return None
    stamped = getattr(fn, "resolved_result", None)
    if is_result_enum(stamped):
        return stamped
    ret = declared_return(fn)
    if hasattr(fn, "resolved_result") and spells_result(ret) and not is_result_enum(ret):
        # A function's spelled return is interned by the resolve pass, never here (#857).
        raise_internal_error(
            "CE0015", message=f"{fn.name}: a spelled Result return reached the backend unstamped")
    if is_result_enum(ret):
        return ret
    arms = signature_result_arms(ret, getattr(fn, "err_type", None))
    if arms is None:
        return None
    structs, enums = codegen.struct_table.by_name, codegen.enum_table.by_name
    return intern_result(codegen, resolve_unknown_type(arms[0], structs, enums),
                         resolve_unknown_type(arms[1], structs, enums))


def call_value_type(codegen: 'LLVMCodegen', fn) -> Optional[Type]:
    """What a call of the callable yields: its channel Result, or its bare return."""
    from sushi_lang.semantics.channel import declared_return
    channel = channel_result_of(codegen, fn)
    return channel if channel is not None else declared_return(fn)


def fn_value_result_type(codegen: 'LLVMCodegen', fn_type) -> Optional[Type]:
    """What a call through a function value yields: the bare return, or its channel Result."""
    if fn_type.err_type is None:
        return fn_type.ok_type
    return intern_result(codegen, fn_type.ok_type, fn_type.err_type)


def declared_return_ll(codegen: 'LLVMCodegen', fn) -> ir.Type:
    """The LLVM return type of a callable: its channel Result, its bare return, or void."""
    value_type = call_value_type(codegen, fn)
    return codegen.types.ll_type(value_type) if value_type else ir.VoidType()


def stamped_result_return(call) -> EnumType:
    """The interned Result that a built-in method with a signature answers (design 8.3).

    A method-level type parameter makes the return type depend on an argument, so the
    backend reads the typecheck pass's stamp and never derives it. A missing stamp is an
    internal error.
    """
    from sushi_lang.semantics.generics.results import is_result_enum
    stamped = getattr(call, "inferred_return_type", None)
    if not (isinstance(stamped, EnumType) and is_result_enum(stamped)):
        raise_internal_error(
            "CE0015", message=f"'{call.method}' has no stamped Result return type")
    return stamped


def build_ok_variant(
    codegen: 'LLVMCodegen',
    result_type: EnumType,
    ok_value: Optional[ir.Value] = None
) -> ir.Value:
    """Construct a Result.Ok(value) LLVM value for a concrete Result enum."""
    return _build_payload_variant(codegen, result_type, "Ok", ok_value)


def build_err_from_return_type(
    codegen: 'LLVMCodegen',
    return_type: Type,
    error_value: Optional[ir.Value],
) -> ir.Value:
    """Construct the Err variant of a function's Result return type.

    An `Err` always holds an error value. The compiler never makes one up, so a call
    without a value is an internal error (#1168, docs/design/error-conversion.md
    section 4).
    """
    from sushi_lang.semantics.generics.results import (
        ensure_result_type_in_table, is_result_enum,
    )
    from sushi_lang.semantics.generics.type_display import display_type
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if error_value is None:
        raise_internal_error("CE0040", variant="Err",
            type=f"{display_type(return_type)}, with no error value")

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
    error_value: Optional[ir.Value],
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
