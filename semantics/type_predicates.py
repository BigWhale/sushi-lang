"""Type checking predicates and type sets for semantic analysis.

This module centralizes type predicates and type sets that were previously
scattered across types.py and type_visitor.py. Extracted as part of the
semantics directory refactoring (Phase 1, Task 1.4).
"""

from typing import Set
from semantics.typesys import Type, BuiltinType


# === Type Sets ===

BUILTIN_INTEGER_TYPES: Set[BuiltinType] = {
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64
}

BUILTIN_FLOAT_TYPES: Set[BuiltinType] = {
    BuiltinType.F32, BuiltinType.F64
}

BUILTIN_NUMERIC_TYPES: Set[BuiltinType] = BUILTIN_INTEGER_TYPES | BUILTIN_FLOAT_TYPES

BUILTIN_STRING_CONVERTIBLE_TYPES: Set[BuiltinType] = BUILTIN_NUMERIC_TYPES | {
    BuiltinType.BOOL, BuiltinType.STRING
}


# === Type Predicates ===

def is_numeric_type(ty: Type) -> bool:
    """Check if a type is numeric (integer or floating-point).

    Args:
        ty: The type to check.

    Returns:
        True if the type is a numeric type (integer or float), False otherwise.

    Examples:
        >>> is_numeric_type(BuiltinType.I32)
        True
        >>> is_numeric_type(BuiltinType.F64)
        True
        >>> is_numeric_type(BuiltinType.STRING)
        False
    """
    return ty in BUILTIN_NUMERIC_TYPES


def is_integer_type(ty: Type) -> bool:
    """Check if a type is an integer type (signed or unsigned).

    Args:
        ty: The type to check.

    Returns:
        True if the type is an integer type, False otherwise.

    Examples:
        >>> is_integer_type(BuiltinType.I32)
        True
        >>> is_integer_type(BuiltinType.U8)
        True
        >>> is_integer_type(BuiltinType.F32)
        False
    """
    return ty in BUILTIN_INTEGER_TYPES


def is_float_type(ty: Type) -> bool:
    """Check if a type is a floating-point type.

    Args:
        ty: The type to check.

    Returns:
        True if the type is a floating-point type, False otherwise.

    Examples:
        >>> is_float_type(BuiltinType.F32)
        True
        >>> is_float_type(BuiltinType.F64)
        True
        >>> is_float_type(BuiltinType.I32)
        False
    """
    return ty in BUILTIN_FLOAT_TYPES


def is_string_convertible(ty: Type) -> bool:
    """Check if a type can be converted to string in string interpolation.

    Supported types for string interpolation:
    - All numeric types (integers and floats)
    - Boolean type
    - String type

    Not supported:
    - Arrays (fixed or dynamic)
    - Structs
    - Enums (including Result<T>, Maybe<T>)
    - Iterator<T>
    - Blank type (~)
    - Special types (stdin, stdout, stderr, file)

    Args:
        ty: The type to check.

    Returns:
        True if the type can be converted to string, False otherwise.

    Examples:
        >>> is_string_convertible(BuiltinType.I32)
        True
        >>> is_string_convertible(BuiltinType.BOOL)
        True
        >>> is_string_convertible(BuiltinType.STRING)
        True
        >>> is_string_convertible(BuiltinType.BLANK)
        False
    """
    if isinstance(ty, BuiltinType):
        return ty in BUILTIN_STRING_CONVERTIBLE_TYPES
    # Arrays, structs, enums, iterators are not supported
    return False
