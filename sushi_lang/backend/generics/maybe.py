"""
Built-in extension methods for Maybe<T> generic enum type.

INLINE EMISSION ONLY. Maybe<T> methods work on-demand for all types.

There is no stdlib IR generation because monomorphizing for all possible user
types is impractical. Unlike Result<T> which only needs to handle a fixed set
of types, Maybe<T> must support any type T that users define (custom structs,
nested generics, etc.). Pre-generating all possible instantiations is not
feasible.

See docs/stdlib/ISSUES.md for why Maybe<T> cannot be moved to stdlib.

Implemented methods:
- is_some() -> bool: Check if value is present (Some variant)
- is_none() -> bool: Check if value is absent (None variant)
- realise(default: T) -> T: Extract Some value or return default if None
- expect(message: string) -> T: Extract Some value or panic with message if None

The Maybe<T> type is a generic enum with two variants:
- Some(T): Contains a value of type T
- None(): Represents absence of value

This module provides ergonomic optional value handling methods that work with
the Maybe<T> type after monomorphization.
"""

from typing import Any, Optional
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type, BuiltinType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.llvm_constants import ONE_I64, ONE_I32
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


# ==============================================================================
# Inline Emission (on-demand code generation)
# ==============================================================================


def is_builtin_maybe_method(method_name: str) -> bool:
    """Check if a method name is a builtin Maybe<T> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Maybe<T> method, False otherwise.
    """
    return method_name in ("is_some", "is_none", "realise", "expect")


def validate_maybe_method_with_validator(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Maybe<T> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        maybe_type: The Maybe<T> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    if call.method == "is_some":
        _validate_maybe_is_some(call, maybe_type, reporter)
    elif call.method == "is_none":
        _validate_maybe_is_none(call, maybe_type, reporter)
    elif call.method == "realise":
        _validate_maybe_realise(call, maybe_type, reporter, validator)
    elif call.method == "expect":
        _validate_maybe_expect(call, maybe_type, reporter, validator)
    else:
        # Unknown method - should not happen if is_builtin_maybe_method was called first
        raise_internal_error("CE0094", method=call.method)


def _validate_maybe_is_some(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any
) -> None:
    """Validate Maybe<T>.is_some() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        maybe_type: The Maybe<T> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_some", expected=0, got=len(call.args))


def _validate_maybe_is_none(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any
) -> None:
    """Validate Maybe<T>.is_none() method call.

    Validates that no arguments are provided.

    Args:
        call: The method call AST node.
        maybe_type: The Maybe<T> enum type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="is_none", expected=0, got=len(call.args))


def _validate_maybe_realise(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Maybe<T>.realise(default) method call.

    Validates that:
    1. Exactly one argument is provided (the default value)
    2. The default value type matches T in Maybe<T>

    Args:
        call: The method call AST node.
        maybe_type: The Maybe<T> enum type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.

    Emits:
        CE2016: If argument count is not exactly 1
        CE2503: If default value type doesn't match T
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc, method="realise", expected=1, got=len(call.args))
        return

    # Extract T from Maybe<T> by getting the Some variant's associated type
    # Maybe<T> has two variants: Some(T) and None()
    some_variant = maybe_type.get_variant("Some")
    if some_variant is None:
        # This shouldn't happen for a valid Maybe<T> enum
        raise_internal_error("CE0092", enum=maybe_type.name)

    if len(some_variant.associated_types) != 1:
        # Some variant should have exactly one associated type (T)
        raise_internal_error("CE0093", got=len(some_variant.associated_types))

    t_type = some_variant.associated_types[0]

    # Check if T is blank type - if so, it's an error
    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    # Validate the default argument's type matches T
    default_arg = call.args[0]

    # Propagate expected type to DotCall nodes for generic enums
    # This allows maybe.realise(Result.Ok(...)) where T is a Result type
    from sushi_lang.semantics.passes.types.utils import propagate_enum_type_to_dotcall
    propagate_enum_type_to_dotcall(validator, default_arg, t_type)

    # First validate the argument expression
    validator.validate_expression(default_arg)

    # Then check type compatibility
    arg_type = validator.infer_expression_type(default_arg)
    if arg_type is not None and not validator._types_compatible(arg_type, t_type):
        er.emit(reporter, er.ERR.CE2503, default_arg.loc,
               expected=str(t_type), got=str(arg_type))


def _validate_maybe_expect(
    call: MethodCall,
    maybe_type: EnumType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Maybe<T>.expect(message) method call.

    Validates that:
    1. Exactly one argument is provided (the error message)
    2. The message is a string type

    Args:
        call: The method call AST node.
        maybe_type: The Maybe<T> enum type (after monomorphization).
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
    arg_type = validator.infer_expression_type(message_arg)
    if arg_type is not None and arg_type != BuiltinType.STRING:
        er.emit(reporter, er.ERR.CE2503, message_arg.loc,
               expected="string", got=str(arg_type))


def emit_builtin_maybe_method(
    codegen: Any,
    call: MethodCall,
    maybe_value: ir.Value,
    maybe_type: EnumType,
    to_i1: bool
) -> ir.Value:
    """Emit LLVM code for Maybe<T> built-in methods.

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        maybe_value: The LLVM value of the Maybe<T> receiver.
        maybe_type: The Maybe<T> enum type (after monomorphization).
        to_i1: Whether to convert result to i1 (for is_some/is_none).

    Returns:
        The LLVM value representing the method call result.

    Raises:
        ValueError: If the method is not recognized or has invalid arguments.
    """
    from sushi_lang.backend.generics.enum_methods_base import emit_enum_tag_check, emit_enum_realise

    if call.method == "is_some":
        return emit_enum_tag_check(codegen, maybe_value, 0, "is_some")
    elif call.method == "is_none":
        return emit_enum_tag_check(codegen, maybe_value, 1, "is_none")
    elif call.method == "realise":
        return emit_enum_realise(codegen, call, maybe_value, maybe_type, "Some", "Maybe")
    elif call.method == "expect":
        return _emit_maybe_expect(codegen, call, maybe_value, maybe_type)
    else:
        raise_internal_error("CE0094", method=call.method)


def _emit_maybe_expect(
    codegen: Any,
    call: MethodCall,
    maybe_value: ir.Value,
    maybe_type: EnumType
) -> ir.Value:
    """Emit LLVM code for maybe.expect(message).

    Maybe<T> enum layout: {i32 tag, [N x i8] data}
    - tag = 0 for Some variant, 1 for None variant
    - data contains the packed value bytes

    If Some: return unpacked value
    If None: print error message and exit(1)

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        maybe_value: The LLVM value of the Maybe<T> enum.
        maybe_type: The Maybe<T> enum type (after monomorphization).

    Returns:
        The extracted value if Some (or exits if None).

    Raises:
        ValueError: If argument count is not exactly 1.
    """
    if len(call.args) != 1:
        raise_internal_error("CE0095", got=len(call.args))

    # Extract T from Maybe<T>
    some_variant = maybe_type.get_variant("Some")
    if some_variant is None:
        raise_internal_error("CE0092", enum=maybe_type.name)

    if len(some_variant.associated_types) != 1:
        raise_internal_error("CE0093", got=len(some_variant.associated_types))

    t_type = some_variant.associated_types[0]

    # Get the LLVM type for T
    value_llvm_type = codegen.types.ll_type(t_type)

    # Extract (is_some, value) from Maybe<T>
    is_some, unpacked_value = codegen.functions._extract_value_from_result_enum(
        maybe_value, value_llvm_type, t_type
    )

    # Create basic blocks for Some and None paths
    some_block = codegen.builder.append_basic_block(name="maybe_expect_some")
    none_block = codegen.builder.append_basic_block(name="maybe_expect_none")
    continue_block = codegen.builder.append_basic_block(name="maybe_expect_continue")

    # Branch based on is_some
    codegen.builder.cbranch(is_some, some_block, none_block)

    # Some block: continue with unpacked value
    codegen.builder.position_at_end(some_block)
    codegen.builder.branch(continue_block)

    # None block: print error message and exit
    codegen.builder.position_at_end(none_block)

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

    # Create a phi node to receive the value from some_block
    phi = codegen.builder.phi(value_llvm_type, name="expect_result")
    phi.add_incoming(unpacked_value, some_block)

    return phi


# ==============================================================================
# Helper Functions for Dynamic Maybe<T> Creation
# ==============================================================================


def ensure_maybe_type_in_table(enum_table: Any, value_type: Type) -> Optional[EnumType]:
    """Ensure that Maybe<T> exists in the enum table, creating it if necessary.

    This is the core function that works with just an enum table, making it usable
    from both semantic analysis and code generation phases.

    Args:
        enum_table: The enum table to register the type in.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The EnumType for Maybe<T>, or None if it couldn't be created.
    """
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo

    # Format the type name
    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    maybe_enum_name = f"Maybe<{type_str}>"

    # Check if it already exists
    if maybe_enum_name in enum_table.by_name:
        return enum_table.by_name[maybe_enum_name]

    # Create the Maybe<T> enum type on the fly
    # Define variants: Some(T) and None()
    some_variant = EnumVariantInfo(name="Some", associated_types=(value_type,))
    none_variant = EnumVariantInfo(name="None", associated_types=())

    # Create the enum type
    maybe_enum = EnumType(
        name=maybe_enum_name,
        variants=(some_variant, none_variant)
    )

    # Register it in the enum table
    enum_table.by_name[maybe_enum_name] = maybe_enum
    enum_table.order.append(maybe_enum_name)

    return maybe_enum


def ensure_maybe_type_exists(codegen: 'LLVMCodegen', value_type: Type) -> Optional[EnumType]:
    """Ensure that Maybe<T> exists in the enum table, creating it if necessary.

    Convenience wrapper for code generation phase that extracts enum_table from codegen.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The EnumType for Maybe<T>, or None if it couldn't be created.
    """
    return ensure_maybe_type_in_table(codegen.enum_table, value_type)


def get_maybe_enum_type(codegen: 'LLVMCodegen', value_type: Type) -> ir.Type:
    """Get the LLVM type for Maybe<T> enum.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The LLVM struct type for Maybe<T>.
    """
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    return codegen.types.ll_type(maybe_enum)


def emit_maybe_some(codegen: 'LLVMCodegen', value_type: Type, value: ir.Value) -> ir.Value:
    """Emit Maybe.Some(value) constructor.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.
        value: The LLVM value to wrap in Some.

    Returns:
        The constructed Maybe<T> enum value with Some variant.
    """
    from sushi_lang.backend.expressions import memory

    # Ensure Maybe<T> type exists
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    # Get the LLVM enum type: {i32 tag, [N x i8] data}
    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    # Get Some variant index (should be 0)
    some_index = maybe_enum.get_variant_index("Some")

    # Create undefined enum value
    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    # Set the tag (discriminant) for Some variant
    tag = ir.Constant(codegen.types.i32, some_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_some_tag")

    # Pack the value into the data field
    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    temp_alloca = codegen.builder.alloca(data_array_type, name="enum_data_temp")

    # Cast to i8* for bitcasting
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Store the value
    value_llvm_type = value.type
    value_ptr_typed = codegen.builder.bitcast(data_ptr, ir.PointerType(value_llvm_type), name="value_ptr_typed")
    codegen.builder.store(value, value_ptr_typed)

    # Load the packed data back into the enum
    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    enum_value = codegen.builder.insert_value(enum_value, packed_data, 1, name="maybe_some_value")

    return enum_value


def emit_maybe_none(codegen: 'LLVMCodegen', value_type: Type) -> ir.Value:
    """Emit Maybe.None() constructor.

    Args:
        codegen: The LLVM codegen instance.
        value_type: The T type parameter for Maybe<T>.

    Returns:
        The constructed Maybe<T> enum value with None variant.
    """
    # Ensure Maybe<T> type exists
    maybe_enum = ensure_maybe_type_exists(codegen, value_type)
    if maybe_enum is None:
        raise_internal_error("CE0047", type=str(value_type))

    # Get the LLVM enum type: {i32 tag, [N x i8] data}
    llvm_enum_type = codegen.types.get_enum_type(maybe_enum)

    # Get None variant index (should be 1)
    none_index = maybe_enum.get_variant_index("None")

    # Create undefined enum value
    enum_value = ir.Constant(llvm_enum_type, ir.Undefined)

    # Set the tag (discriminant) for None variant
    tag = ir.Constant(codegen.types.i32, none_index)
    enum_value = codegen.builder.insert_value(enum_value, tag, 0, name="maybe_none_tag")

    # None variant has no associated data, so we just set an undefined data field
    data_array_type = llvm_enum_type.elements[1]  # [N x i8] array
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    enum_value = codegen.builder.insert_value(enum_value, undef_data, 1, name="maybe_none_value")

    return enum_value
