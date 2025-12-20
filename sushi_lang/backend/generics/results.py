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


# ==============================================================================
# Inline Emission (on-demand code generation)
# ==============================================================================


def is_builtin_result_method(method_name: str) -> bool:
    """Check if a method name is a builtin Result<T, E> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Result<T, E> method, False otherwise.
    """
    return method_name in ("is_ok", "is_err", "realise", "expect", "err")


def validate_result_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    # CRITICAL: Annotate the MethodCall with the resolved Result<T, E> type
    # This allows the backend to use the correct type during code generation
    # instead of relying on unreliable LLVM type matching
    call.resolved_enum_type = result_type

    if call.method == "is_ok":
        _validate_result_is_ok(call, result_type, reporter)
    elif call.method == "is_err":
        _validate_result_is_err(call, result_type, reporter)
    elif call.method == "realise":
        validate_result_realise_method_with_validator(call, result_type, reporter, validator)
    elif call.method == "expect":
        _validate_result_expect(call, result_type, reporter, validator)
    elif call.method == "err":
        _validate_result_err(call, result_type, reporter)
    else:
        # Unknown method - should not happen if is_builtin_result_method was called first
        raise_internal_error("CE0094", method=call.method)


def _validate_result_is_ok(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.is_ok() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_ok", expected=0, got=len(call.args))


def _validate_result_is_err(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.is_err() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_err", expected=0, got=len(call.args))


def _validate_result_err(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any
) -> None:
    """Validate Result<T, E>.err() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="err", expected=0, got=len(call.args))


def _validate_result_expect(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T, E>.expect(message) method call.

    Validates that:
    1. Exactly one argument is provided (the error message)
    2. The message is a string type

    Args:
        call: The method call AST node.
        result_type: The Result<T, E> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.

    Emits:
        CE2016: If argument count is not exactly 1
        CE2503: If message is not a string
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="expect", expected=1, got=len(call.args))
        return

    # Validate the message argument is a string
    message_arg = call.args[0]

    # First validate the argument expression
    validator.validate_expression(message_arg)

    # Then check it's a string
    from sushi_lang.semantics.typesys import BuiltinType
    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
               expected="string", got=str(arg_type))


def validate_result_realise_method_with_validator(
    call: MethodCall,
    result_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Result<T>.realise(default) method call.

    Validates that:
    1. Exactly one argument is provided (the default value)
    2. The default value type matches T in Result<T>

    Args:
        call: The method call AST node.
        result_type: The Result<T> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.

    Emits:
        CE2502: If argument count is not exactly 1
        CE2503: If default value type doesn't match T
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2502, call.loc, got=len(call.args))
        return

    # Extract T from Result<T> by getting the Ok variant's associated type
    # Result<T> has two variants: Ok(T) and Err()
    # We need to find the Ok variant and extract its associated type
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        # This shouldn't happen for a valid Result<T> enum
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        # Ok variant should have exactly one associated type (T)
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    t_type = ok_variant.associated_types[0]

    # Check if T is blank type - if's an error
    from sushi_lang.semantics.typesys import BuiltinType
    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    # Validate the default argument's type matches T
    default_arg = call.args[0]

    # Resolve GenericTypeRef to concrete type for propagation
    # This handles cases like HashMap<i32, string> which may be stored as GenericTypeRef
    from sushi_lang.semantics.type_resolution import TypeResolver
    from sushi_lang.semantics.typesys import StructType
    type_resolver = TypeResolver(validator.struct_table.by_name, validator.enum_table.by_name)
    resolved_t_type = type_resolver.resolve_generic_type_ref(t_type)

    # Propagate expected type to DotCall nodes for generic enums (before validation)
    # This allows result.realise(Maybe.None()) to work correctly
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall, propagate_struct_type_to_dotcall
    propagate_enum_type_to_dotcall(validator, default_arg, resolved_t_type)

    # Propagate expected type to DotCall nodes for generic structs (before validation)
    # This allows result.realise(HashMap.new()) to work correctly
    if isinstance(resolved_t_type, StructType):
        propagate_struct_type_to_dotcall(validator, default_arg, resolved_t_type)

    # First validate the argument expression
    validator.validate_expression(default_arg)

    # Then check type compatibility
    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
               expected=str(t_type), got=str(arg_type))


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
    from sushi_lang.backend.llvm_constants import ONE_I64, ONE_I32

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


def ensure_result_type_in_table(enum_table: Any, ok_type: Type, err_type: Type) -> Optional[EnumType]:
    """Ensure that Result<T, E> exists in the enum table, creating it if necessary.

    This is a convenience wrapper around ResultBuilder for backward compatibility.
    New code should use ResultBuilder directly for better caching and the full API.

    Args:
        enum_table: The enum table to register the type in.
        ok_type: The T type parameter for Result<T, E>.
        err_type: The E type parameter for Result<T, E>.

    Returns:
        The EnumType for Result<T, E>, or None if it couldn't be created.
    """
    from sushi_lang.backend.generics.result_builder import ResultBuilder
    builder = ResultBuilder(enum_table)
    return builder.ensure_type(ok_type, err_type)
