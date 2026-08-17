"""Enum constructor validation."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, EnumType
from sushi_lang.semantics.ast import EnumConstructor, DotCall, Name
from ..compatibility import types_compatible
from sushi_lang.semantics.generics.type_display import display_type

if TYPE_CHECKING:
    from .. import TypeValidator
    from sushi_lang.semantics.ast import EnumVariant


def validate_enum_constructor(validator: 'TypeValidator', constructor: EnumConstructor) -> None:
    """Validate enum variant constructor - variant exists, argument count and types."""
    enum_type = resolve_enum_type(validator, constructor)
    if enum_type is None:
        return

    variant = validate_variant_exists(validator, enum_type, constructor)
    if variant is None:
        return

    propagate_generic_types_to_nested_constructors(validator, constructor, variant)

    validate_constructor_arguments(validator, constructor, variant, enum_type)


def resolve_enum_type(validator: 'TypeValidator', constructor: EnumConstructor) -> Optional[EnumType]:
    """Resolve constructor to concrete or generic enum type."""
    if hasattr(constructor, 'resolved_enum_type') and constructor.resolved_enum_type is not None:
        return constructor.resolved_enum_type

    enum_name = constructor.enum_name

    if enum_name in validator.enum_table.by_name:
        return validator.enum_table.by_name[enum_name]

    if enum_name in validator.generic_enum_table.by_name:
        # A generic enum constructor with no concrete type context. `resolved_enum_type` is
        # normally stamped by the let/return validators before this point, so full checking
        # happens against the monomorphized enum. Nested arguments are still validated, for
        # the propagation `Result.Ok(Maybe.Some(42))` needs.
        for arg in constructor.args:
            validator.validate_expression(arg)
        return None

    er.emit(validator.reporter, er.ERR.CE2001, constructor.enum_name_span or constructor.loc,
           name=enum_name)
    return None


def validate_variant_exists(
    validator: 'TypeValidator', enum_type: EnumType, constructor: EnumConstructor
) -> Optional['EnumVariant']:
    """Check variant exists in enum and return it."""
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
    """Set resolved_enum_type for nested generic enum constructors."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    for _i, (arg, expected_type) in enumerate(zip(actual_args, expected_types, strict=False)):
        resolved_type = expected_type
        if isinstance(expected_type, GenericTypeRef):
            concrete_name = str(expected_type)
            if concrete_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[concrete_name]
            else:
                continue

        if not isinstance(resolved_type, EnumType):
            continue

        if isinstance(arg, EnumConstructor):
            if arg.enum_name in validator.generic_enum_table.by_name:
                arg.resolved_enum_type = resolved_type
        elif isinstance(arg, DotCall):
            if isinstance(arg.receiver, Name):
                receiver_name = arg.receiver.id
                if receiver_name in validator.generic_enum_table.by_name:
                    arg.resolved_enum_type = resolved_type


def validate_constructor_arguments(
    validator: 'TypeValidator', constructor: EnumConstructor, variant: 'EnumVariant', enum_type: EnumType
) -> None:
    """Validate argument count and types for enum constructor."""
    variant_name = constructor.variant_name
    expected_types = list(variant.associated_types)
    actual_args = constructor.args

    # Special check for Result.Ok() with zero arguments (CE2036)
    # This provides a more helpful error message than the generic "wrong argument count"
    if (enum_type.name.startswith("Result<") and variant_name == "Ok" and
        len(actual_args) == 0 and len(expected_types) == 1):
        expected_type = expected_types[0]
        if expected_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2036, constructor.loc)
            return

    if len(actual_args) != len(expected_types):
        er.emit(validator.reporter, er.ERR.CE2050, constructor.loc,
               variant=variant_name, expected=len(expected_types), got=len(actual_args))

    for _i, (arg, expected_type) in enumerate(zip(actual_args, expected_types, strict=False)):
        validator.validate_expression(arg)

        from sushi_lang.semantics.typesys import UnknownType
        from sushi_lang.semantics.type_resolution import resolve_unknown_type
        resolved_type = expected_type
        if isinstance(expected_type, UnknownType):
            resolved_type = resolve_unknown_type(expected_type, validator.struct_table.by_name, validator.enum_table.by_name)

        arg_type = validator.infer_expression_type(arg)
        if arg_type is not None and not types_compatible(validator, arg_type, resolved_type):
            er.emit(validator.reporter, er.ERR.CE2049, arg.loc,
                   variant=variant_name, expected=display_type(resolved_type), got=display_type(arg_type))

    for i in range(len(expected_types), len(actual_args)):
        validator.validate_expression(actual_args[i])
