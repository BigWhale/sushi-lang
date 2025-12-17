# semantics/passes/types/utils.py
"""
Shared utilities for type validation.

This module contains helper functions used across multiple validation modules:
- Type name validation
- Parameter validation and registration
- Array destruction tracking
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from internals.report import Span
from internals import errors as er
from semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, StructType, EnumType, ResultType, ReferenceType
from semantics.type_resolution import resolve_unknown_type

if TYPE_CHECKING:
    from semantics.ast import Param, Expr
    from . import TypeValidator


def validate_type_name(validator: 'TypeValidator', type_obj: Optional[Type], span: Optional[Span]) -> None:
    """Validate that a type name is known/valid."""
    if type_obj is None:
        return

    # Check if this is a GenericTypeRef
    from semantics.generics.types import GenericTypeRef
    if isinstance(type_obj, GenericTypeRef):
        # Special handling for Result<T, E> - it doesn't get monomorphized, it gets resolved to ResultType
        if type_obj.base_name == "Result" and len(type_obj.type_args) == 2:
            # Validate type arguments recursively
            for type_arg in type_obj.type_args:
                validate_type_name(validator, type_arg, span)
            # Result<T, E> is valid - it will be resolved to ResultType during type checking
            return

        # Validate that the generic type base exists (check both enums and structs)
        is_generic_enum = type_obj.base_name in validator.generic_enum_table.by_name
        is_generic_struct = type_obj.base_name in validator.generic_struct_table.by_name

        if not is_generic_enum and not is_generic_struct:
            er.emit(validator.reporter, er.ERR.CE2001, span, name=type_obj.base_name)
            return

        # Validate all type arguments recursively
        for type_arg in type_obj.type_args:
            validate_type_name(validator, type_arg, span)

        # Check if the monomorphized version exists in the enum or struct table
        type_args_str = ", ".join(str(arg) for arg in type_obj.type_args)
        concrete_name = f"{type_obj.base_name}<{type_args_str}>"

        if concrete_name not in validator.enum_table.by_name and concrete_name not in validator.struct_table.by_name:
            # Monomorphized type should exist after monomorphization pass
            # If not, it means this instantiation wasn't collected
            er.emit(validator.reporter, er.ERR.CE2001, span, name=concrete_name)
        return

    # Check if this is an unknown type
    if isinstance(type_obj, UnknownType):
        # Check if it's a struct type
        if type_obj.name in validator.struct_table.by_name:
            # Valid struct type - this is okay
            return
        # Check if it's an enum type
        if type_obj.name in validator.enum_table.by_name:
            # Valid enum type - this is okay
            return
        # Unknown type that's not a struct or enum
        er.emit(validator.reporter, er.ERR.CE2001, span, name=type_obj.name)
    elif isinstance(type_obj, BuiltinType) and type_obj not in validator.known_types:
        # This shouldn't happen with current builtin types, but good to check
        er.emit(validator.reporter, er.ERR.CE2001, span, name=str(type_obj))
    elif isinstance(type_obj, ArrayType):
        # Blank type cannot be used as array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        # Recursively validate the base type of the array
        validate_type_name(validator, type_obj.base_type, span)
        # Validate array size (CE2010: Array size must be positive integer literal)
        if type_obj.size <= 0:
            er.emit(validator.reporter, er.ERR.CE2010, span, size=type_obj.size)
    elif isinstance(type_obj, DynamicArrayType):
        # Blank type cannot be used as dynamic array base type
        if type_obj.base_type == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, span)
            return
        # Recursively validate the base type
        validate_type_name(validator, type_obj.base_type, span)
    elif isinstance(type_obj, ResultType):
        # ResultType is a valid semantic type - recursively validate ok_type and err_type
        validate_type_name(validator, type_obj.ok_type, span)
        validate_type_name(validator, type_obj.err_type, span)


def validate_and_register_parameters(validator: 'TypeValidator', params: List['Param']) -> None:
    """Validate parameter types and register them in the variable_types table.

    This helper method eliminates duplication between _validate_function() and
    _validate_extension_method(). It validates each parameter's type annotation
    and registers valid parameters in the variable_types table.

    Args:
        validator: The TypeValidator instance.
        params: List of parameter definitions to validate and register.
    """
    for param in params:
        validate_type_name(validator, param.ty, param.type_span)

        # Blank type cannot be used for parameters
        if param.ty == BuiltinType.BLANK:
            er.emit(validator.reporter, er.ERR.CE2032, param.type_span)
            continue

        # Handle ReferenceType by registering the full reference type
        # This is important for pattern matching and method resolution on reference params
        if isinstance(param.ty, ReferenceType):
            # Resolve the referenced type if it's an UnknownType
            ref_type = param.ty.referenced_type
            if isinstance(ref_type, UnknownType):
                ref_type = resolve_unknown_type(ref_type, validator.struct_table.by_name, validator.enum_table.by_name)
            # Create a new ReferenceType with the resolved inner type
            resolved_ref = ReferenceType(
                referenced_type=ref_type,
                mutability=param.ty.mutability
            )
            validator.variable_types[param.name] = resolved_ref
            continue

        if isinstance(param.ty, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType, ResultType)):
            validator.variable_types[param.name] = param.ty
        elif isinstance(param.ty, UnknownType):
            # Resolve UnknownType to StructType/EnumType for struct/enum-typed parameters
            resolved_type = resolve_unknown_type(param.ty, validator.struct_table.by_name, validator.enum_table.by_name)
            if resolved_type != param.ty:
                validator.variable_types[param.name] = resolved_type
        else:
            # Handle GenericTypeRef and other types
            from semantics.generics.types import GenericTypeRef
            if isinstance(param.ty, GenericTypeRef):
                # Resolve GenericTypeRef to monomorphized EnumType or StructType
                type_args_str = ", ".join(str(arg) for arg in param.ty.type_args)
                type_name = f"{param.ty.base_name}<{type_args_str}>"

                # Check both enum and struct tables
                resolved_type = None
                if type_name in validator.enum_table.by_name:
                    resolved_type = validator.enum_table.by_name[type_name]
                elif type_name in validator.struct_table.by_name:
                    resolved_type = validator.struct_table.by_name[type_name]

                if resolved_type is not None:
                    validator.variable_types[param.name] = resolved_type
                    param.ty = resolved_type  # Update AST node for backend

                    # CRITICAL: Also update the FuncSig parameter in the function table
                    # This ensures function call validation uses the resolved type for propagation
                    if validator.current_function and validator.current_function.name in validator.func_table.by_name:
                        func_sig = validator.func_table.by_name[validator.current_function.name]
                        for sig_param in func_sig.params:
                            if sig_param.name == param.name:
                                sig_param.ty = resolved_type
                                break
                else:
                    # GenericTypeRef should have been monomorphized - this is an error
                    # but validation has already been done in validate_type_name
                    pass


def mark_array_destroyed(validator: 'TypeValidator', name: str) -> None:
    """Mark a dynamic array as destroyed in the current scope."""
    if validator.destroyed_arrays:
        validator.destroyed_arrays[-1].add(name)


def is_array_destroyed(validator: 'TypeValidator', name: str) -> bool:
    """Check if a dynamic array has been destroyed in any current scope."""
    for destroyed_set in validator.destroyed_arrays:
        if name in destroyed_set:
            return True
    return False


def push_destroyed_scope(validator: 'TypeValidator') -> None:
    """Push a new scope for tracking destroyed arrays."""
    validator.destroyed_arrays.append(set())


def pop_destroyed_scope(validator: 'TypeValidator') -> None:
    """Pop the current scope for tracking destroyed arrays."""
    if validator.destroyed_arrays:
        validator.destroyed_arrays.pop()


def propagate_enum_type_to_dotcall(
    validator: 'TypeValidator',
    arg: 'Expr',
    expected_type: Optional[Type]
) -> None:
    """Propagate expected enum type to DotCall nodes for generic enums.

    This allows enum constructors like Maybe.None(), Result.Ok(), Either.Left(), etc.
    to be used directly as function/method arguments without requiring
    intermediate variables.

    The function sets the `resolved_enum_type` attribute on DotCall nodes
    when all conditions are met:
    1. arg is a DotCall node with a Name receiver
    2. The receiver is a generic enum name (built-in or user-defined)
    3. The expected_type can be resolved to a concrete EnumType

    Args:
        validator: The TypeValidator instance (provides enum tables)
        arg: The argument expression (checked for DotCall pattern)
        expected_type: The expected type for this argument (may be None)

    Example:
        # Before validation:
        propagate_enum_type_to_dotcall(validator, call.args[0], param.ty)
        validator.validate_expression(call.args[0])

    Note:
        This function should be called BEFORE validate_expression() to ensure
        type propagation happens before enum constructor validation.
    """
    # Early exit if no expected type
    if expected_type is None:
        return

    # Use the unified propagation function which handles recursion
    from semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, arg, expected_type)


def propagate_struct_type_to_dotcall(
    validator: 'TypeValidator',
    arg: 'Expr',
    expected_type: Optional[Type]
) -> None:
    """Propagate expected struct type to DotCall nodes for generic structs.

    This allows struct constructors like Own.alloc() to be used directly
    as function/method arguments or in let statements without requiring
    intermediate variables.

    The function sets the `resolved_struct_type` attribute on DotCall nodes
    when all conditions are met:
    1. arg is a DotCall node with a Name receiver
    2. The receiver is a known generic struct name (like Own)
    3. The expected_type can be resolved to a concrete StructType

    Args:
        validator: The TypeValidator instance (provides struct tables)
        arg: The argument expression (checked for DotCall pattern)
        expected_type: The expected type for this argument (may be None)

    Example:
        # Before validation:
        propagate_struct_type_to_dotcall(validator, call.args[0], param.ty)
        validator.validate_expression(call.args[0])

    Note:
        This function should be called BEFORE validate_expression() to ensure
        type propagation happens before struct constructor validation.
    """
    # Early exit if no expected type
    if expected_type is None:
        return

    # Use the unified propagation function which handles recursion
    from semantics.passes.types.propagation import propagate_types_to_value
    propagate_types_to_value(validator, arg, expected_type)
