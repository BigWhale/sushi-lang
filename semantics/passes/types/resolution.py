# semantics/passes/types/resolution.py
"""
Type resolution utilities for semantic analysis.

This module provides utilities for resolving declared types (GenericTypeRef,
UnknownType) to concrete types (ResultType, EnumType, StructType, etc.).

Resolution happens BEFORE type propagation and validation, establishing the
expected types for expressions.

Extracted from validate_return_statement() and validate_let_statement() to
eliminate duplication and centralize type resolution logic.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from internals import errors as er
from semantics.typesys import (
    BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType,
    UnknownType, ResultType
)
from semantics.generics.types import GenericTypeRef
from semantics.type_resolution import resolve_unknown_type

if TYPE_CHECKING:
    from . import TypeValidator
    from semantics.typesys import Type
    from internals.report import Span


def resolve_return_type_to_result(validator: 'TypeValidator',
                                   declared_type: 'Type',
                                   err_type_node: Optional['Type']) -> ResultType:
    """Convert function return type to ResultType.

    Handles three cases:
    1. Explicit Result<T, E> (GenericTypeRef) → resolve to ResultType
    2. Implicit T | E → wrap in Result<T, E> with custom error
    3. Implicit T → wrap in Result<T, StdError>

    Args:
        validator: The type validator instance
        declared_type: The declared return type from function signature
        err_type_node: The error type node (for T | E syntax), or None

    Returns:
        ResultType representing the actual return type

    Consolidates lines 144-173 from validate_return_statement().
    """
    resolved_type = declared_type

    # Case 1: Explicit Result<T, E> - resolve GenericTypeRef to ResultType
    if isinstance(declared_type, GenericTypeRef):
        if declared_type.base_name == "Result" and len(declared_type.type_args) == 2:
            # Special handling for Result<T, E> - resolve to ResultType
            resolved_type = resolve_unknown_type(
                declared_type,
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
        else:
            # For other generic types, look up monomorphized concrete type
            type_args_str = ", ".join(str(arg) for arg in declared_type.type_args)
            enum_name = f"{declared_type.base_name}<{type_args_str}>"
            if enum_name in validator.enum_table.by_name:
                resolved_type = validator.enum_table.by_name[enum_name]

    # Case 2/3: Implicit Result wrapping (T | E or just T)
    if not isinstance(resolved_type, ResultType):
        # Function declares T or T | E (not explicit Result<T, E>)
        # Implicitly wraps in Result<T, E>

        if err_type_node:
            # Case 2: Custom error type (fn foo() T | MyError)
            err_type = resolve_unknown_type(
                err_type_node,
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
        else:
            # Case 3: Default to StdError (fn foo() T)
            err_type = validator.enum_table.by_name.get("StdError")

        if err_type:
            resolved_type = ResultType(ok_type=resolved_type, err_type=err_type)

    return resolved_type


def resolve_variable_type(validator: 'TypeValidator',
                          declared_type: 'Type',
                          type_span: 'Span') -> 'Type':
    """Resolve variable type from declaration.

    Handles:
    - Builtin/Array/Struct/Enum types (already resolved)
    - UnknownType → resolved EnumType/StructType
    - GenericTypeRef for Result<T, E> → ResultType
    - GenericTypeRef for HashMap<K, V> → concrete StructType (with validation)
    - GenericTypeRef for other generics → concrete EnumType/StructType

    Args:
        validator: The type validator instance
        declared_type: The declared type from let statement
        type_span: Source location for error reporting

    Returns:
        Resolved concrete type

    Consolidates lines 41-101 from validate_let_statement().
    """
    # Already resolved types - return as-is
    if isinstance(declared_type, (BuiltinType, ArrayType, DynamicArrayType, StructType, EnumType)):
        return declared_type

    # UnknownType → resolve to StructType or EnumType
    if isinstance(declared_type, UnknownType):
        resolved = resolve_unknown_type(
            declared_type,
            validator.struct_table.by_name,
            validator.enum_table.by_name
        )
        return resolved

    # GenericTypeRef → resolve based on base name
    if isinstance(declared_type, GenericTypeRef):
        # Special case: Result<T, E> → ResultType (semantic type, not enum)
        if declared_type.base_name == "Result" and len(declared_type.type_args) == 2:
            ok_type = resolve_unknown_type(
                declared_type.type_args[0],
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
            err_type = resolve_unknown_type(
                declared_type.type_args[1],
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
            return ResultType(ok_type=ok_type, err_type=err_type)

        # Special case: HashMap<K, V> → validate key type first
        if declared_type.base_name == "HashMap" and len(declared_type.type_args) >= 1:
            key_type = declared_type.type_args[0]
            if isinstance(key_type, DynamicArrayType):
                er.emit(validator.reporter, er.ERR.CE2058, type_span, key_type=str(key_type))

        # General case: Monomorphized generic → concrete EnumType/StructType
        # Build type name: Maybe<i32> -> "Maybe<i32>", HashMap<string, i32> -> "HashMap<string, i32>"
        type_args_str = ", ".join(str(arg) for arg in declared_type.type_args)
        concrete_name = f"{declared_type.base_name}<{type_args_str}>"

        # Try enum table first (Maybe, Either, user-defined generic enums)
        if concrete_name in validator.enum_table.by_name:
            return validator.enum_table.by_name[concrete_name]

        # Try struct table second (Own, Box, Pair, HashMap, user-defined generic structs)
        if concrete_name in validator.struct_table.by_name:
            return validator.struct_table.by_name[concrete_name]

    # Fallback: return as-is (should be rare)
    return declared_type
