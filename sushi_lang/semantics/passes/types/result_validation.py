"""Result pattern validation utilities."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Tuple, List, Union, cast

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
        validator.validate_expression(args[0])
        error_arg = args[0]

        error_value_type = extract_error_value_type(validator, error_arg)

        if error_value_type and expected_err_type and not types_compatible(validator, error_value_type, expected_err_type):
            er.emit(validator.reporter, er.ERR.CE2039, loc,
                   expected=display_type(expected_err_type), got=display_type(error_value_type))


def spells_a_result(node: Expr) -> bool:
    """Whether the expression is written `Result.Ok(...)` or `Result.Err(...)`.

    The CE2030 decision (#848). It reads the written expression and no type, so a
    template body takes it as written (#1070).
    """
    is_result, variant_name = is_result_pattern(node)
    return is_result and variant_name in ("Ok", "Err")


def is_result_pattern(node: Expr) -> Tuple[bool, Optional[str]]:
    """Detect if node is Result.Ok/Err across all AST node types."""
    if isinstance(node, EnumConstructor):
        if node.enum_name == "Result":
            return (True, node.variant_name)

    elif isinstance(node, DotCall):
        if isinstance(node.receiver, Name) and node.receiver.id == "Result":
            return (True, node.method)

    elif isinstance(node, MethodCall):
        if isinstance(node.receiver, Name) and node.receiver.id == "Result":
            return (True, node.method)

    return (False, None)


def validate_result_pattern(validator: 'TypeValidator', node: Expr,
                            expected_type: 'Type') -> None:
    """Check the payload of a `Result.Ok(...)` / `Result.Err(...)` against the channel.

    The caller decided with `spells_a_result` that the node is one of the two; this
    checks only the payload (CE2031, CE2039).
    """
    from sushi_lang.semantics.generics.results import is_result_enum, result_ok_err

    _, variant_name = is_result_pattern(node)
    if is_result_enum(expected_type):
        compare_type, expected_error_type = result_ok_err(expected_type)
    else:
        compare_type = expected_type
        expected_error_type = None

    args = cast(Union[EnumConstructor, DotCall, MethodCall], node).args
    if variant_name == "Ok":
        validate_result_ok_value(validator, args, compare_type, node.loc)
    else:
        validate_result_err_value(validator, args, expected_error_type, node.loc)
