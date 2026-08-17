"""Built-in extension methods for Result<T, E> generic enum type."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error


def emit_builtin_result_method(
    codegen: Any,
    call: MethodCall,
    result_value: ir.Value,
    result_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Result<T, E> built-in methods."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check, emit_enum_realise

    if call.method == "is_ok":
        return emit_enum_tag_check(codegen, result_value, 0, "is_ok")
    elif call.method == "is_err":
        return emit_enum_tag_check(codegen, result_value, 1, "is_err")
    elif call.method == "realise":
        return emit_enum_realise(codegen, call, result_value, result_type, "Ok", "Result")
    elif call.method == "expect":
        return _emit_result_expect(codegen, call, result_value, result_type)
    elif call.method == "err":
        return _emit_result_err(codegen, result_value, result_type)
    else:
        raise_internal_error("CE0094", method=call.method)


def _emit_result_expect(
    codegen: Any,
    call: MethodCall,
    result_value: ir.Value,
    result_type: EnumType
) -> ir.Value:
    """Emit `result.expect(message)` -- Ok payload, or "ERROR: msg" to stderr and exit(1)."""
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_expect
    return emit_enum_expect(codegen, call, result_value, result_type,
                            success_variant_name="Ok", label="result",
                            missing_variant_code="CE0089", assoc_count_code="CE0090")


def _emit_result_err(
    codegen: Any,
    result_value: ir.Value,
    result_type: EnumType
) -> ir.Value:
    """Emit LLVM code for result.err()."""
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none

    err_variant = result_type.get_variant("Err")
    if err_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(err_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(err_variant.associated_types))

    e_type = err_variant.associated_types[0]

    error_llvm_type = codegen.types.ll_type(e_type)

    # Extract (is_ok, error_value) from Result<T, E>
    # Note: We're extracting the Err variant's data, but _extract_value_from_result_enum
    # always extracts from the data field regardless of tag
    is_ok, error_value = codegen.functions._extract_value_from_result_enum(
        result_value, error_llvm_type, e_type
    )

    ok_block = codegen.builder.append_basic_block(name="result_err_ok")
    err_block = codegen.builder.append_basic_block(name="result_err_err")
    continue_block = codegen.builder.append_basic_block(name="result_err_continue")

    codegen.builder.cbranch(is_ok, ok_block, err_block)

    codegen.builder.position_at_end(ok_block)
    none_value = emit_maybe_none(codegen, e_type)
    codegen.builder.branch(continue_block)

    codegen.builder.position_at_end(err_block)
    some_value = emit_maybe_some(codegen, e_type, error_value)
    codegen.builder.branch(continue_block)

    codegen.builder.position_at_end(continue_block)

    from sushi_lang.backend.generics.maybe import get_maybe_enum_type
    maybe_llvm_type = get_maybe_enum_type(codegen, e_type)

    phi = codegen.builder.phi(maybe_llvm_type, name="err_result")
    phi.add_incoming(none_value, ok_block)
    phi.add_incoming(some_value, err_block)

    return phi


def _extract_ok_type_from_result(result_type: EnumType) -> Type:
    """Extract the T type from Result<T, E> enum."""
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    return ok_variant.associated_types[0]


