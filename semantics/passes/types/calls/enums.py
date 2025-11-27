# semantics/passes/types/calls/enums.py
"""
Enum constructor validation.

Handles validation for:
- Enum variant constructors
- Generic enum constructors (Result<T>, Maybe<T>)
- Nested enum constructors
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from internals import errors as er
from semantics.typesys import BuiltinType, EnumType
from semantics.ast import EnumConstructor, Call, DotCall, Name
from ..compatibility import types_compatible

if TYPE_CHECKING:
    from .. import TypeValidator
    from semantics.enums.variants import EnumVariant


def validate_enum_constructor(validator: 'TypeValidator', constructor: EnumConstructor) -> None:
    """Validate enum variant constructor - variant exists, argument count and types.

    This is the main orchestrator method that delegates to focused helper methods.
    """
    # Step 1: Resolve enum type (handles concrete and generic enums)
    enum_type = resolve_enum_type(validator, constructor)
    if enum_type is None:
        return

    # Step 2: Validate variant exists in the enum
    variant = validate_variant_exists(validator, enum_type, constructor)
    if variant is None:
        return

    # Step 3: Propagate generic types to nested enum constructors
    propagate_generic_types_to_nested_constructors(validator, constructor, variant)

    # Step 4: Validate constructor arguments (count and types)
    validate_constructor_arguments(validator, constructor, variant, enum_type)


def resolve_enum_type(validator: 'TypeValidator', constructor: EnumConstructor) -> Optional[EnumType]:
    """Resolve constructor to concrete or generic enum type.

    Args:
        validator: The TypeValidator instance.
        constructor: The enum constructor to resolve.

    Returns:
        The resolved EnumType if found, None otherwise.
    """
    # Priority 1: Check if resolved_enum_type is already set (for generic enums like Result<T>)
    # This is set by _validate_return_statement or _validate_let_statement
    if hasattr(constructor, 'resolved_enum_type') and constructor.resolved_enum_type is not None:
        return constructor.resolved_enum_type

    enum_name = constructor.enum_name

    # Priority 2: Check if enum exists as a concrete enum in the enum table
    if enum_name in validator.enum_table.by_name:
        return validator.enum_table.by_name[enum_name]

    # Priority 3: Check if it's a known generic enum (like Result, Maybe)
    if enum_name in validator.generic_enum_table.by_name:
        # Fallback path: Generic enum constructor without concrete type context
        # This handles edge cases where a generic enum (Result, Maybe) is used without
        # type resolution from let/return statements. In practice, resolved_enum_type
        # should be set by _validate_return_statement or _validate_let_statement before
        # validation reaches this point, allowing full type checking against the
        # monomorphized enum type (e.g., Result<i32>, Maybe<string>).
        #
        # We still validate nested arguments to ensure type propagation for cases like:
        # Result.Ok(Maybe.Some(42)) where the nested Maybe.Some needs processing.
        for arg in constructor.args:
            validator.validate_expression(arg)
        return None

    # Priority 4: Unknown enum type
    er.emit(validator.reporter, er.ERR.CE2001, constructor.enum_name_span or constructor.loc,
           name=enum_name)
    return None


def validate_variant_exists(
    validator: 'TypeValidator', enum_type: EnumType, constructor: EnumConstructor
) -> Optional['EnumVariant']:
    """Check variant exists in enum and return it.

    Args:
        validator: The TypeValidator instance.
        enum_type: The enum type to check.
        constructor: The constructor with the variant name.

    Returns:
        The EnumVariant if found, None otherwise.
    """
    variant_name = constructor.variant_name
    variant = enum_type.get_variant(variant_name)

    if variant is None:
        er.emit(validator.reporter, er.ERR.CE2045, constructor.variant_name_span or constructor.loc,
               variant=variant_name, enum=enum_type.name)
        return None

    return variant


def propagate_generic_types_to_nested_constructors(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant'
) -> None:
    """Set resolved_enum_type for nested generic enum constructors.

    This propagates type information from outer constructors to nested ones,
    enabling proper type checking for cases like Maybe.Some(Result.Ok(42)).

    Args:
        validator: The TypeValidator instance.
        constructor: The outer enum constructor.
        variant: The variant being constructed.
    """
    from semantics.generics.types import GenericTypeRef

    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Propagate expected types to nested generic enum constructors BEFORE validation
    for i, (arg, expected_type) in enumerate(zip(actual_args, expected_types)):
        # Resolve GenericTypeRef to concrete EnumType
        # This handles cases like Maybe<Result<i32>>.Some where associated_types[0] is GenericTypeRef
        resolved_type = expected_type
        if isinstance(expected_type, GenericTypeRef):
            # Convert GenericTypeRef to concrete enum name (e.g., "Result<i32>")
            concrete_name = str(expected_type)
            if concrete_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[concrete_name]
            else:
                # Enum not monomorphized - skip propagation for this argument
                continue

        # Only propagate if we have a concrete EnumType
        if not isinstance(resolved_type, EnumType):
            continue

        # Handle both EnumConstructor and DotCall nodes
        if isinstance(arg, EnumConstructor):
            if arg.enum_name in validator.generic_enum_table.by_name:
                # Set the resolved enum type for the nested constructor
                # This must happen BEFORE validate_expression is called
                arg.resolved_enum_type = resolved_type
        elif isinstance(arg, DotCall):
            # Check if this DotCall is an enum constructor (receiver is a type name)
            if isinstance(arg.receiver, Name):
                receiver_name = arg.receiver.id
                if receiver_name in validator.generic_enum_table.by_name:
                    # Set the resolved enum type for the DotCall
                    # This allows Result.Ok(), Maybe.Some(), etc. to work as nested arguments
                    arg.resolved_enum_type = resolved_type


def validate_constructor_arguments(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant', enum_type: EnumType
) -> None:
    """Validate argument count and types for enum constructor.

    Args:
        validator: The TypeValidator instance.
        constructor: The enum constructor to validate.
        variant: The variant being constructed.
        enum_type: The resolved enum type.
    """
    variant_name = constructor.variant_name
    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Special check for Result.Ok() with zero arguments (CE2036)
    # This provides a more helpful error message than the generic "wrong argument count"
    if (enum_type.name.startswith("Result<") and variant_name == "Ok" and
        len(actual_args) == 0 and len(expected_types) == 1):
        # Check if the expected type is blank type
        expected_type = expected_types[0]
        if expected_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2036, constructor.loc)
            return

    # Check argument count
    if len(actual_args) != len(expected_types):
        er.emit(validator.reporter, er.ERR.CE2050, constructor.loc,
               variant=variant_name, expected=len(expected_types), got=len(actual_args))
        # Still continue with validation of provided arguments

    # Validate each argument type against corresponding associated type
    for i, (arg, expected_type) in enumerate(zip(actual_args, expected_types)):
        # Recursively validate the argument expression
        validator.validate_expression(arg)

        # Resolve UnknownType to StructType/EnumType if needed
        from semantics.typesys import UnknownType
        from semantics.type_resolution import resolve_unknown_type
        resolved_type = expected_type
        if isinstance(expected_type, UnknownType):
            resolved_type = resolve_unknown_type(expected_type, validator.struct_table.by_name, validator.enum_table.by_name)

        # Check type compatibility
        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_type):
            er.emit(validator.reporter, er.ERR.CE2049, arg.loc,
                   variant=variant_name, expected=str(resolved_type), got=str(arg_type))

    # Validate any excess arguments (if more args than expected)
    for i in range(len(expected_types), len(actual_args)):
        validator.validate_expression(actual_args[i])
