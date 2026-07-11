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

See docs/stdlib/ISSUES.md for why Result<T, E> cannot be moved to stdlib.
"""

from typing import Any, Optional
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.results import ensure_result_type_in_table


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
    """Emit LLVM code for result.expect(message).

    Result<T, E> enum layout: {i32 tag, [N x i8] data}
    - tag = 0 for Ok variant, 1 for Err variant
    - data contains the packed value bytes

    If Ok: return unpacked value
    If Err: print error message and exit(1)

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        result_value: The LLVM value of the Result<T, E> enum.
        result_type: The Result<T, E> enum type (after monomorphization).

    Returns:
        The extracted value if Ok (or exits if Err).

    Raises:
        ValueError: If argument count is not exactly 1.
    """
    from sushi_lang.backend.constants.llvm_values import ONE_I64, ONE_I32

    if len(call.args) != 1:
        raise_internal_error("CE0095", got=len(call.args))

    # Extract T from Result<T, E>
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    t_type = ok_variant.associated_types[0]

    # Get the LLVM type for T
    value_llvm_type = codegen.types.ll_type(t_type)

    # Extract (is_ok, value) from Result<T, E>
    is_ok, unpacked_value = codegen.functions._extract_value_from_result_enum(
        result_value, value_llvm_type, t_type
    )

    # Create basic blocks for Ok and Err paths
    ok_block = codegen.builder.append_basic_block(name="result_expect_ok")
    err_block = codegen.builder.append_basic_block(name="result_expect_err")
    continue_block = codegen.builder.append_basic_block(name="result_expect_continue")

    # Branch based on is_ok
    codegen.builder.cbranch(is_ok, ok_block, err_block)

    # Ok block: continue with unpacked value
    codegen.builder.position_at_end(ok_block)
    codegen.builder.branch(continue_block)

    # Err block: print error message and exit
    codegen.builder.position_at_end(err_block)

    # Emit the error message expression (fat pointer string)
    error_message = codegen.expressions.emit_expr(call.args[0])

    # Print error message to stderr using runtime function
    # Format: "ERROR: <message>\n"
    error_prefix_fat = codegen.runtime.strings.emit_string_literal("ERROR: ")
    newline_fat = codegen.runtime.strings.emit_string_literal("\n")

    # Get stderr file pointer
    stderr_ptr = codegen.builder.load(codegen.runtime.libc_stdio.stderr_handle)

    # Get fwrite function
    fwrite_fn = codegen.runtime.libc_stdio.fwrite

    # Write "ERROR: " to stderr using fwrite
    error_prefix_ptr = codegen.builder.extract_value(error_prefix_fat, 0, name="error_prefix_ptr")
    error_prefix_len = codegen.builder.extract_value(error_prefix_fat, 1, name="error_prefix_len")
    error_prefix_len_i64 = codegen.builder.zext(error_prefix_len, ir.IntType(64), name="error_prefix_len_i64")
    codegen.builder.call(fwrite_fn, [error_prefix_ptr, ONE_I64, error_prefix_len_i64, stderr_ptr])

    # Write user message to stderr using fwrite
    error_message_ptr = codegen.builder.extract_value(error_message, 0, name="error_msg_ptr")
    error_message_len = codegen.builder.extract_value(error_message, 1, name="error_msg_len")
    error_message_len_i64 = codegen.builder.zext(error_message_len, ir.IntType(64), name="error_msg_len_i64")
    codegen.builder.call(fwrite_fn, [error_message_ptr, ONE_I64, error_message_len_i64, stderr_ptr])

    # Write newline to stderr using fwrite
    newline_ptr = codegen.builder.extract_value(newline_fat, 0, name="newline_ptr")
    newline_len = codegen.builder.extract_value(newline_fat, 1, name="newline_len")
    newline_len_i64 = codegen.builder.zext(newline_len, ir.IntType(64), name="newline_len_i64")
    codegen.builder.call(fwrite_fn, [newline_ptr, ONE_I64, newline_len_i64, stderr_ptr])

    # Call exit(1)
    codegen.builder.call(codegen.runtime.libc_process.exit, [ONE_I32])

    # Unreachable after exit, but LLVM requires a terminator
    codegen.builder.unreachable()

    # Continue block: return the unpacked value
    codegen.builder.position_at_end(continue_block)

    # Create a phi node to receive the value from ok_block
    phi = codegen.builder.phi(value_llvm_type, name="expect_result")
    phi.add_incoming(unpacked_value, ok_block)

    return phi


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


