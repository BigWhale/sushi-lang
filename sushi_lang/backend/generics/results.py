"""
Built-in extension methods for Result<T, E> generic enum type.

Implemented methods:
- is_ok() -> bool: Check if Result is Ok variant
- is_err() -> bool: Check if Result is Err variant
- realise(default: T) -> T: Extract Ok value or return default if Err
- expect(message: string) -> T: Extract Ok value or panic with message if Err
- err() -> Maybe<E>: Extract error value or Maybe.None if Ok

The Result<T, E> type is a generic enum with two variants:
- Ok(T): Contains a successful value of type T
- Err(E): Contains an error value of type E

This module provides ergonomic error handling methods that work with
the Result<T, E> type after monomorphization.

ARCHITECTURE:
This module provides INLINE EMISSION ONLY. Result<T, E> methods work on-demand
for all types (built-in and user-defined) during compilation. There is no
stdlib IR generation because monomorphizing for all possible user types is
impractical.

"""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error


# ==============================================================================
# Inline Emission (on-demand code generation)
# ==============================================================================


def emit_builtin_result_method(
    codegen: Any,
    call: MethodCall,
    result_value: ir.Value,
    result_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Result<T, E> built-in methods.

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        result_value: The LLVM value of the Result<T, E> receiver.
        result_type: The Result<T, E> enum type (after monomorphization).
        to_i1: Whether to convert result to i1 (for is_ok/is_err).

    Returns:
        The LLVM value representing the method call result.

    Raises:
        ValueError: If the method is not recognized or has invalid arguments.
    """
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
    """Emit `result.expect(message)` -- Ok payload, or "ERROR: msg" to stderr and exit(1).

    Thin adapter over the shared `emit_enum_expect`; the Maybe spelling is its twin.
    """
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_expect
    return emit_enum_expect(codegen, call, result_value, result_type,
                            success_variant_name="Ok", label="result",
                            missing_variant_code="CE0089", assoc_count_code="CE0090")


def _emit_result_err(
    codegen: Any,
    result_value: ir.Value,
    result_type: EnumType
) -> ir.Value:
    """Emit LLVM code for result.err().

    Result<T, E> enum layout: {i32 tag, [N x i8] data}
    - tag = 0 for Ok variant, 1 for Err variant
    - data contains the packed value bytes

    Returns Maybe<E>:
    - If Ok: Maybe.None()
    - If Err: Maybe.Some(error_value)

    Args:
        codegen: The LLVM code generator instance.
        result_value: The LLVM value of the Result<T, E> enum.
        result_type: The Result<T, E> enum type (after monomorphization).

    Returns:
        Maybe<E> enum value.
    """
    from sushi_lang.backend.generics.maybe import emit_maybe_some, emit_maybe_none

    # Extract E from Result<T, E>
    err_variant = result_type.get_variant("Err")
    if err_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(err_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(err_variant.associated_types))

    e_type = err_variant.associated_types[0]

    # Get the LLVM type for E
    error_llvm_type = codegen.types.ll_type(e_type)

    # Extract (is_ok, error_value) from Result<T, E>
    # Note: We're extracting the Err variant's data, but _extract_value_from_result_enum
    # always extracts from the data field regardless of tag
    is_ok, error_value = codegen.functions._extract_value_from_result_enum(
        result_value, error_llvm_type, e_type
    )

    # Create basic blocks for Ok and Err paths
    ok_block = codegen.builder.append_basic_block(name="result_err_ok")
    err_block = codegen.builder.append_basic_block(name="result_err_err")
    continue_block = codegen.builder.append_basic_block(name="result_err_continue")

    # Branch based on is_ok
    codegen.builder.cbranch(is_ok, ok_block, err_block)

    # Ok block: return Maybe.None()
    codegen.builder.position_at_end(ok_block)
    none_value = emit_maybe_none(codegen, e_type)
    codegen.builder.branch(continue_block)

    # Err block: return Maybe.Some(error_value)
    codegen.builder.position_at_end(err_block)
    some_value = emit_maybe_some(codegen, e_type, error_value)
    codegen.builder.branch(continue_block)

    # Continue block: phi to merge the two paths
    codegen.builder.position_at_end(continue_block)

    # Get the Maybe<E> LLVM type
    from sushi_lang.backend.generics.maybe import get_maybe_enum_type
    maybe_llvm_type = get_maybe_enum_type(codegen, e_type)

    # Create phi node to select between None and Some
    phi = codegen.builder.phi(maybe_llvm_type, name="err_result")
    phi.add_incoming(none_value, ok_block)
    phi.add_incoming(some_value, err_block)

    return phi


def _extract_ok_type_from_result(result_type: EnumType) -> Type:
    """Extract the T type from Result<T, E> enum.

    Helper function to get the associated type from the Ok variant.

    Args:
        result_type: The Result<T, E> enum type.

    Returns:
        The T type from Result<T, E>.

    Raises:
        RuntimeError: If Result enum is malformed.
    """
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    return ok_variant.associated_types[0]


