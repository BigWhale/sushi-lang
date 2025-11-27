# semantics/passes/types/result_validation.py
"""
Result pattern validation utilities.

This module provides utilities for validating Result.Ok() and Result.Err() patterns
across different AST node types (EnumConstructor, DotCall, MethodCall).

Extracted from validate_return_statement() to eliminate triple duplication.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Tuple, List

from internals import errors as er
from semantics.typesys import ResultType
from semantics.ast import EnumConstructor, DotCall, MethodCall, Name, MemberAccess, Expr
from .compatibility import types_compatible

if TYPE_CHECKING:
    from . import TypeValidator
    from internals.report import Span
    from semantics.typesys import Type


def extract_error_value_type(validator: 'TypeValidator', error_arg: Expr) -> Optional['Type']:
    """Extract type from error argument, handling MemberAccess enum patterns.

    For MemberAccess nodes like ErrorB.Error, the type is the base enum.
    Otherwise, infers the type from the expression.

    Args:
        validator: The type validator instance
        error_arg: The error expression to extract type from

    Returns:
        The inferred type of the error value, or None if unable to infer

    Consolidates 8-line duplication that appeared 3x in validate_return_statement.
    """
    if isinstance(error_arg, MemberAccess) and isinstance(error_arg.receiver, Name):
        enum_name = error_arg.receiver.id
        if enum_name in validator.enum_table.by_name:
            return validator.enum_table.by_name[enum_name]
        else:
            return validator.infer_expression_type(error_arg)
    else:
        return validator.infer_expression_type(error_arg)


def validate_result_ok_value(validator: 'TypeValidator', args: List[Expr],
                             expected_ok_type: 'Type', loc: 'Span') -> None:
    """Validate Result.Ok(value) argument type matches expected ok_type.

    Emits CE2031 if type mismatch occurs.

    Args:
        validator: The type validator instance
        args: The arguments to Result.Ok()
        expected_ok_type: The expected type for the Ok value
        loc: Source location for error reporting

    Consolidates lines 322-328, 356-362, 395-401 from validate_return_statement.
    """
    if args:
        value_type = validator.infer_expression_type(args[0])
        if value_type and expected_ok_type and not types_compatible(validator, value_type, expected_ok_type):
            er.emit(validator.reporter, er.ERR.CE2031, loc,
                   expected=str(expected_ok_type), got=str(value_type))


def validate_result_err_value(validator: 'TypeValidator', args: List[Expr],
                              expected_err_type: Optional['Type'], loc: 'Span') -> None:
    """Validate Result.Err(error) argument type matches expected err_type.

    Emits CE2039 if type mismatch occurs.
    Uses extract_error_value_type() for consistent error type extraction.

    Args:
        validator: The type validator instance
        args: The arguments to Result.Err()
        expected_err_type: The expected error type (can be None)
        loc: Source location for error reporting

    Consolidates lines 329-350, 363-382, 402-421 from validate_return_statement.
    """
    if args:
        # First validate the error argument
        validator.validate_expression(args[0])
        error_arg = args[0]

        # Extract error type using unified logic
        error_value_type = extract_error_value_type(validator, error_arg)

        # Check compatibility with expected error type
        if error_value_type and expected_err_type and not types_compatible(validator, error_value_type, expected_err_type):
            er.emit(validator.reporter, er.ERR.CE2039, loc,
                   expected=str(expected_err_type), got=str(error_value_type))


def is_result_pattern(node: Expr) -> Tuple[bool, Optional[str]]:
    """Detect if node is Result.Ok/Err across all AST node types.

    Handles EnumConstructor, DotCall, and MethodCall nodes uniformly.

    Args:
        node: The AST node to check

    Returns:
        (is_result, variant_name):
            - (True, "Ok"|"Err") if node is a Result pattern
            - (False, None) otherwise

    This provides a unified interface for Result pattern detection across
    different parsing representations (old-style vs unified vs legacy).
    """
    if isinstance(node, EnumConstructor):
        # Old-style enum constructor parsing
        if node.enum_name == "Result":
            return (True, node.variant_name)

    elif isinstance(node, DotCall):
        # DotCall: unified X.Y(args) node (current standard)
        if isinstance(node.receiver, Name) and node.receiver.id == "Result":
            return (True, node.method)

    elif isinstance(node, MethodCall):
        # Old parsing: Result.Ok() was parsed as MethodCall (legacy support)
        if isinstance(node.receiver, Name) and node.receiver.id == "Result":
            return (True, node.method)

    return (False, None)


def validate_result_pattern(validator: 'TypeValidator', node: Expr,
                           expected_type: ResultType) -> bool:
    """Main orchestrator for Result pattern validation.

    Validates that the node is a Result.Ok() or Result.Err() pattern with
    correct argument types. Emits appropriate error codes (CE2031, CE2039).

    Args:
        validator: The type validator instance
        node: The AST node to validate (should be return value expression)
        expected_type: The expected ResultType from the function signature

    Returns:
        True if node is a valid Result.Ok/Err pattern, False otherwise

    This function replaces 109 lines of triply-duplicated code (lines 313-421)
    from validate_return_statement() with a single unified implementation.

    Note: For MethodCall nodes, includes legacy enum table checks for backward
    compatibility with old parsing behavior.
    """
    is_result, variant_name = is_result_pattern(node)

    if not is_result:
        # Additional check for MethodCall: verify it's an enum constructor
        # This maintains backward compatibility with legacy parsing
        if isinstance(node, MethodCall):
            if isinstance(node.receiver, Name) and (
                node.receiver.id in validator.enum_table.by_name or
                node.receiver.id in validator.generic_enum_table.by_name
            ):
                # It's an enum constructor but not Result
                return False
        return False

    # Extract ok_type for Ok() validation
    compare_type = expected_type.ok_type if isinstance(expected_type, ResultType) else expected_type

    # Extract err_type for Err() validation
    expected_error_type = expected_type.err_type if isinstance(expected_type, ResultType) else None

    # Get node location for error reporting
    loc = node.loc

    # Get arguments based on node type
    if isinstance(node, EnumConstructor):
        args = node.args
    elif isinstance(node, DotCall):
        args = node.args
    elif isinstance(node, MethodCall):
        args = node.args
    else:
        return False

    # Validate based on variant
    if variant_name == "Ok":
        validate_result_ok_value(validator, args, compare_type, loc)
    elif variant_name == "Err":
        validate_result_err_value(validator, args, expected_error_type, loc)
    else:
        # Unknown variant name
        return False

    return True
