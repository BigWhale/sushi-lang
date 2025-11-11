"""
Built-in extension methods for Result<T> generic enum type.

Implemented methods:
- realise(default: T) -> T: Extract Ok value or return default if Err

The Result<T> type is a generic enum with two variants:
- Ok(T): Contains a successful value of type T
- Err(): Represents an error with no value

This module provides ergonomic error handling methods that work with
the Result<T> type after monomorphization.

ARCHITECTURE:
This module provides INLINE EMISSION ONLY. Result<T> methods work on-demand
for all types (built-in and user-defined) during compilation. There is no
stdlib IR generation because monomorphizing for all possible user types is
impractical.

See docs/stdlib/ISSUES.md for why Result<T> cannot be moved to stdlib.
"""

from typing import Any, Optional
from semantics.ast import MethodCall
from semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from internals import errors as er
from internals.errors import raise_internal_error


# ==============================================================================
# Inline Emission (on-demand code generation)
# ==============================================================================


def is_builtin_result_method(method_name: str) -> bool:
    """Check if a method name is a builtin Result<T> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Result<T> method, False otherwise.
    """
    return method_name == "realise"


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
    from semantics.typesys import BuiltinType
    if t_type == BuiltinType.BLANK:
        er.emit(reporter, er.ERR.CE2506, call.loc)
        return

    # Validate the default argument's type matches T
    default_arg = call.args[0]

    # Propagate expected type to DotCall nodes for generic enums (before validation)
    # This allows result.realise(Maybe.None()) to work correctly
    from semantics.passes.types.utils import propagate_enum_type_to_dotcall
    propagate_enum_type_to_dotcall(validator, default_arg, t_type)

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
    """Emit LLVM code for Result<T> built-in methods.

    Args:
        codegen: The LLVM code generator instance.
        call: The method call AST node.
        result_value: The LLVM value of the Result<T> receiver.
        result_type: The Result<T> enum type (after monomorphization).
        to_i1: Whether to convert result to i1 (not used for realise).

    Returns:
        The LLVM value representing the method call result.

    Raises:
        ValueError: If the method is not recognized or has invalid arguments.
    """
    from backend.generics.enum_methods_base import emit_enum_realise

    if call.method == "realise":
        return emit_enum_realise(codegen, call, result_value, result_type, "Ok", "Result")
    else:
        raise_internal_error("CE0094", method=call.method)


def _extract_ok_type_from_result(result_type: EnumType) -> Type:
    """Extract the T type from Result<T> enum.

    Helper function to get the associated type from the Ok variant.

    Args:
        result_type: The Result<T> enum type.

    Returns:
        The T type from Result<T>.

    Raises:
        RuntimeError: If Result enum is malformed.
    """
    ok_variant = result_type.get_variant("Ok")
    if ok_variant is None:
        raise_internal_error("CE0089", enum=result_type.name)

    if len(ok_variant.associated_types) != 1:
        raise_internal_error("CE0090", got=len(ok_variant.associated_types))

    return ok_variant.associated_types[0]


def ensure_result_type_in_table(enum_table: Any, value_type: Type) -> Optional[EnumType]:
    """Ensure that Result<T> exists in the enum table, creating it if necessary.

    This is the core function that works with just an enum table, making it usable
    from both semantic analysis and code generation phases.

    Args:
        enum_table: The enum table to register the type in.
        value_type: The T type parameter for Result<T>.

    Returns:
        The EnumType for Result<T>, or None if it couldn't be created.
    """
    from semantics.typesys import EnumType, EnumVariantInfo, BuiltinType

    # Format the type name
    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    result_enum_name = f"Result<{type_str}>"

    # Check if it already exists
    if result_enum_name in enum_table.by_name:
        return enum_table.by_name[result_enum_name]

    # Create the Result<T> enum type on the fly
    # Define variants: Ok(T) and Err()
    ok_variant = EnumVariantInfo(name="Ok", associated_types=(value_type,))
    err_variant = EnumVariantInfo(name="Err", associated_types=())

    # Create enum type
    result_enum = EnumType(
        name=result_enum_name,
        variants=(ok_variant, err_variant)
    )

    # Register in enum table
    enum_table.by_name[result_enum_name] = result_enum
    enum_table.order.append(result_enum_name)

    return result_enum
