# semantics/passes/types/result_validation.py
"""Result pattern validation utilities."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Tuple, List

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.ast import EnumConstructor, DotCall, MethodCall, Name, MemberAccess, Expr
from .compatibility import types_compatible

if TYPE_CHECKING:
    from . import TypeValidator
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.typesys import Type


def extract_error_value_type(validator: 'TypeValidator', error_arg: Expr) -> Optional['Type']:
    """Extract type from error argument, handling MemberAccess enum patterns."""
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
    """Validate Result.Ok(value) argument type matches expected ok_type."""
    if args:
        value_type = validator.infer_expression_type(args[0])
        if value_type and expected_ok_type and not types_compatible(validator, value_type, expected_ok_type):
            er.emit(validator.reporter, er.ERR.CE2031, loc,
                   expected=display_type(expected_ok_type), got=display_type(value_type))


def validate_result_err_value(validator: 'TypeValidator', args: List[Expr],
                              expected_err_type: Optional['Type'], loc: 'Span') -> None:
    """Validate Result.Err(error) argument type matches expected err_type."""
    if args:
        # First validate the error argument
        validator.validate_expression(args[0])
        error_arg = args[0]

        # Extract error type using unified logic
        error_value_type = extract_error_value_type(validator, error_arg)

        # Check compatibility with expected error type
        if error_value_type and expected_err_type and not types_compatible(validator, error_value_type, expected_err_type):
            er.emit(validator.reporter, er.ERR.CE2039, loc,
                   expected=display_type(expected_err_type), got=display_type(error_value_type))


def is_result_pattern(node: Expr) -> Tuple[bool, Optional[str]]:
    """Detect if node is Result.Ok/Err across all AST node types."""
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
                           expected_type: 'Type') -> bool:
    """Main orchestrator for Result pattern validation."""
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

    # Extract the (ok, err) payloads the variants are validated against. The expected type is
    # the function's Result in whichever shape it currently has -- the interned EnumType, or the

    from sushi_lang.semantics.generics.results import is_result_enum, result_ok_err

    if is_result_enum(expected_type):
        compare_type, expected_error_type = result_ok_err(expected_type)
    else:
        compare_type = expected_type
        expected_error_type = None

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
