# semantics/passes/types/compatibility.py
"""
Type compatibility checking for type validation.

This module contains functions for checking type compatibility:
- Assignment compatibility validation
- Return type compatibility validation
- Generic type compatibility checking
- Cast validity checking
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from internals.report import Span
from internals import errors as er
from semantics.typesys import Type, BuiltinType, UnknownType, ArrayType, DynamicArrayType, EnumType, ReferenceType, BorrowMode
from semantics.ast import Expr, ArrayLiteral, DynamicArrayNew, DynamicArrayFrom, IntLit
from semantics.type_resolution import resolve_unknown_type
from .inference import infer_dynamic_array_from_type

if TYPE_CHECKING:
    from . import TypeValidator


def validate_assignment_compatibility(validator: 'TypeValidator', declared_type: Optional[Type], value_expr: Expr, declared_span: Optional[Span], value_span: Optional[Span]) -> None:
    """Validate that value expression type matches declared type (CE2002)."""
    if declared_type is None:
        return  # Can't validate without declared type

    # First validate the expression (this will catch undefined functions, etc.)
    validator.validate_expression(value_expr)

    # Special validation for array literals assigned to array types
    if isinstance(declared_type, ArrayType) and isinstance(value_expr, ArrayLiteral):
        # Check array literal size matches declared size (CE2011)
        if len(value_expr.elements) != declared_type.size:
            er.emit(validator.reporter, er.ERR.CE2011, value_span,
                   got=len(value_expr.elements), expected=declared_type.size)
            return

    # Special validation for dynamic array constructors
    if isinstance(declared_type, DynamicArrayType):
        if isinstance(value_expr, DynamicArrayNew):
            # new() constructor - type matches by definition
            return
        elif isinstance(value_expr, DynamicArrayFrom):
            # from(array_literal) - validate element types match
            # Pass expected type for contextual type inference
            inferred_type = infer_dynamic_array_from_type(validator, value_expr, expected_type=declared_type)
            if inferred_type is None:
                return  # Error already reported or empty array
            if not types_compatible(validator, inferred_type, declared_type):
                er.emit(validator.reporter, er.ERR.CE2002, value_span,
                       got=str(inferred_type), expected=str(declared_type))
            return
        # For other expressions (function calls, ??, etc.), fall through to general validation

    # Infer the type of the value expression
    value_type = validator.infer_expression_type(value_expr)
    if value_type is None:
        return  # Can't validate without inferred type

    # Check for type mismatch (using types_compatible to handle struct types)
    if not types_compatible(validator, value_type, declared_type):
        er.emit(validator.reporter, er.ERR.CE2002, value_span,
               got=str(value_type), expected=str(declared_type))


def validate_return_compatibility(validator: 'TypeValidator', expected_type: Type, return_expr: Expr, return_span: Optional[Span]) -> None:
    """Validate that return expression type matches function return type (CE2003)."""
    # First validate the expression (this will catch undefined functions, etc.)
    validator.validate_expression(return_expr)

    # Infer the type of the return expression
    actual_type = validator.infer_expression_type(return_expr)
    if actual_type is None:
        return  # Can't validate without inferred type

    # Check for type mismatch
    if not types_compatible(validator, actual_type, expected_type):
        er.emit(validator.reporter, er.ERR.CE2003, return_span,
               got=str(actual_type), expected=str(expected_type))


def resolve_generic_type_ref(validator: 'TypeValidator', ty: Type) -> Type:
    """Resolve GenericTypeRef to monomorphized EnumType or StructType.

    Args:
        validator: The TypeValidator instance.
        ty: The type to resolve (may be GenericTypeRef or any other type).

    Returns:
        The resolved EnumType or StructType if ty is a GenericTypeRef with a monomorphized version,
        otherwise returns ty unchanged.
    """
    from semantics.generics.types import GenericTypeRef

    if isinstance(ty, GenericTypeRef):
        # Build type name from generic type ref: Result<i32> -> "Result<i32>", Box<i32> -> "Box<i32>"
        type_args_str = ", ".join(str(arg) for arg in ty.type_args)
        concrete_name = f"{ty.base_name}<{type_args_str}>"

        # Check if it's a monomorphized enum
        if concrete_name in validator.enum_table.by_name:
            return validator.enum_table.by_name[concrete_name]

        # Check if it's a monomorphized struct
        if concrete_name in validator.struct_table.by_name:
            return validator.struct_table.by_name[concrete_name]

    return ty


def compare_resolved_types(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Compare two resolved types (no GenericTypeRef or UnknownType resolution).

    This method assumes both types have been through resolution and performs
    direct comparison with recursive handling for container types.

    Args:
        validator: The TypeValidator instance.
        actual: The actual type (already resolved).
        expected: The expected type (already resolved).

    Returns:
        True if types are compatible, False otherwise.
    """
    from semantics.typesys import UnknownType, DynamicArrayType, ArrayType

    # Direct equality check
    if actual == expected:
        return True

    # Handle UnknownType -> StructType/EnumType resolution
    if isinstance(actual, UnknownType) and isinstance(expected, UnknownType):
        # Both are unknown types - check if they refer to the same struct or enum
        return actual.name == expected.name

    if isinstance(actual, UnknownType):
        # Try to resolve actual to struct or enum and compare
        resolved = resolve_unknown_type(actual, validator.struct_table.by_name, validator.enum_table.by_name)
        return resolved == expected

    if isinstance(expected, UnknownType):
        # Try to resolve expected to struct or enum and compare
        resolved = resolve_unknown_type(expected, validator.struct_table.by_name, validator.enum_table.by_name)
        return actual == resolved

    # Handle array types with recursive comparison
    if isinstance(actual, DynamicArrayType) and isinstance(expected, DynamicArrayType):
        return types_compatible(validator, actual.base_type, expected.base_type)

    if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
        return actual.size == expected.size and types_compatible(validator, actual.base_type, expected.base_type)

    return False


def types_compatible(validator: 'TypeValidator', actual: Type, expected: Type) -> bool:
    """Check if two types are compatible (handles UnknownType resolution to StructType/EnumType).

    This is a recursive comparison that handles:
    - Direct type equality
    - UnknownType -> StructType/EnumType resolution
    - GenericTypeRef -> EnumType/StructType resolution (for generic types like Result<T>, Box<T>)
    - ResultType compatibility (semantic Result<T, E> vs GenericTypeRef)
    - ReferenceType compatibility with coercion (&poke T -> &peek T allowed)
    - Recursive comparison for container types (arrays, etc.)
    """
    from semantics.typesys import ResultType
    from semantics.generics.types import GenericTypeRef

    # Quick check for direct equality
    if actual == expected:
        return True

    # Reference type compatibility with coercion
    # - &poke T can be passed where &peek T is expected (safe downgrade)
    # - &peek T cannot be passed where &poke T is expected
    if isinstance(actual, ReferenceType) and isinstance(expected, ReferenceType):
        # First check if referenced types are compatible
        if not types_compatible(validator, actual.referenced_type, expected.referenced_type):
            return False

        # Check mutability compatibility
        if actual.mutability == expected.mutability:
            return True  # Same mutability
        elif actual.mutability == BorrowMode.POKE and expected.mutability == BorrowMode.PEEK:
            return True  # &poke -> &peek coercion allowed
        else:
            return False  # &peek -> &poke not allowed

    # Special handling for ResultType compatibility
    # Case 1: actual is ResultType, expected is GenericTypeRef("Result", [T, E])
    if isinstance(actual, ResultType) and isinstance(expected, GenericTypeRef):
        if expected.base_name == "Result" and len(expected.type_args) == 2:
            # Recursively compare T and E
            ok_compatible = types_compatible(validator, actual.ok_type, expected.type_args[0])
            err_compatible = types_compatible(validator, actual.err_type, expected.type_args[1])
            return ok_compatible and err_compatible

    # Case 2: actual is GenericTypeRef("Result", [T, E]), expected is ResultType
    if isinstance(actual, GenericTypeRef) and isinstance(expected, ResultType):
        if actual.base_name == "Result" and len(actual.type_args) == 2:
            # Recursively compare T and E
            ok_compatible = types_compatible(validator, actual.type_args[0], expected.ok_type)
            err_compatible = types_compatible(validator, actual.type_args[1], expected.err_type)
            return ok_compatible and err_compatible

    # Case 3: both are ResultType - use built-in equality
    if isinstance(actual, ResultType) and isinstance(expected, ResultType):
        return actual == expected

    # Step 1: Resolve GenericTypeRef to monomorphized EnumType or StructType (if applicable)
    resolved_actual = resolve_generic_type_ref(validator, actual)
    resolved_expected = resolve_generic_type_ref(validator, expected)

    # Step 2: Compare the resolved types (handles UnknownType and recursive array comparison)
    return compare_resolved_types(validator, resolved_actual, resolved_expected)


def is_valid_cast(source_type: Type, target_type: Type) -> bool:
    """Check if a cast from source_type to target_type is valid.

    Args:
        source_type: The type being cast from.
        target_type: The type being cast to.

    Returns:
        True if the cast is valid, False otherwise.
    """
    # Same type is always valid (no-op cast)
    if source_type == target_type:
        return True

    # Only allow casts between numeric types for now
    # This implements the Rust-style explicit-only casting system
    numeric_types = {
        BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
        BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
        BuiltinType.F32, BuiltinType.F64
    }

    # Both source and target must be numeric types
    if source_type in numeric_types and target_type in numeric_types:
        return True

    # No other casts are allowed (strings, arrays, etc.)
    return False
