# semantics/passes/types/expressions.py
"""
Expression validation for type validation.

This module contains validation functions for various expression types:
- Array literals
- Index access
- Cast expressions
- Try expressions (?? operator)
- Bitwise operations
- Boolean conditions
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from internals import errors as er
from semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType
from semantics.ast import ArrayLiteral, IndexAccess, CastExpr, TryExpr, BinaryOp, UnaryOp, Expr, IntLit
from semantics.type_predicates import is_numeric_type
from .compatibility import is_valid_cast

if TYPE_CHECKING:
    from . import TypeValidator


def validate_array_literal(validator: 'TypeValidator', expr: ArrayLiteral) -> None:
    """Validate array literal - all elements must have same type."""
    if not expr.elements:
        # Empty array literal - can't infer type without context
        return

    # Validate all element expressions
    for element in expr.elements:
        validator.validate_expression(element)

    # Check type consistency of all elements (CE2013)
    first_element_type = validator.infer_expression_type(expr.elements[0])
    if first_element_type is not None:
        for i, element in enumerate(expr.elements[1:], 1):
            element_type = validator.infer_expression_type(element)
            if element_type is not None and element_type != first_element_type:
                er.emit(validator.reporter, er.ERR.CE2013, element.loc,
                       expected=str(first_element_type), got=str(element_type))


def validate_index_access(validator: 'TypeValidator', expr: IndexAccess) -> None:
    """Validate array indexing - array must be array type, index must be int."""
    # Validate array expression
    validator.validate_expression(expr.array)

    # Validate index expression
    validator.validate_expression(expr.index)

    # Check that index is integer type
    index_type = validator.infer_expression_type(expr.index)
    if index_type is not None and index_type != BuiltinType.I32:
        er.emit(validator.reporter, er.ERR.CE2002, expr.index.loc,
               got=str(index_type), expected=str(BuiltinType.I32))

    # Check that array expression is actually an array type
    array_type = validator.infer_expression_type(expr.array)
    if array_type is not None and not isinstance(array_type, (ArrayType, DynamicArrayType)):
        # If we can infer the type and it's not an array, that's a type error
        er.emit(validator.reporter, er.ERR.CE2002, expr.array.loc,
               got=str(array_type), expected="array type")

    # Compile-time bounds checking for fixed arrays with constant indices
    if isinstance(array_type, ArrayType) and isinstance(expr.index, IntLit):
        index_value = expr.index.value
        array_size = array_type.size

        # Check for out-of-bounds access (negative indices or >= array size)
        if index_value < 0 or index_value >= array_size:
            er.emit(validator.reporter, er.ERR.CE2012, expr.index.loc,
                   index=index_value, size=array_size)


def validate_cast_expression(validator: 'TypeValidator', expr: CastExpr) -> None:
    """Validate a cast expression and check if the cast is valid.

    Args:
        validator: The TypeValidator instance.
        expr: The cast expression to validate.
    """
    # First validate the source expression
    validator.validate_expression(expr.expr)

    # Get the source and target types
    source_type = validator.infer_expression_type(expr.expr)
    target_type = expr.target_type

    # If we can't infer the source type, skip validation
    if source_type is None:
        return

    # Check if the cast is valid
    if not is_valid_cast(source_type, target_type):
        er.emit(validator.reporter, er.ERR.CE2014, expr.loc,
               source=str(source_type), target=str(target_type))


def validate_try_expression(validator: 'TypeValidator', expr: 'TryExpr') -> None:
    """Validate ?? operator usage.

    Validates that:
    1. The inner expression is Result<T>, Maybe<T>, or another result-like enum (CE2507)
    2. The enclosing function returns Result<T> or another result-like enum (CE2508)

    Supported enum patterns:
    - Result-like: Ok(value) and Err(...) variants (e.g., Result<T>, FileResult)
    - Maybe-like: Some(value) and None() variants (e.g., Maybe<T>)

    When ?? is applied to Maybe<T>:
    - Some(value) unwraps to value
    - None() propagates as Result.Err() to the enclosing function

    Args:
        validator: The TypeValidator instance.
        expr: The TryExpr node to validate.
    """
    # Import TryExpr for type checking
    from semantics.ast import TryExpr

    # First validate the inner expression
    validator.validate_expression(expr.expr)

    # Get the type of the inner expression
    inner_type = validator.infer_expression_type(expr.expr)

    # Check if inner expression is a supported enum (Result-like or Maybe-like)
    if inner_type is not None:
        if not isinstance(inner_type, EnumType):
            # CE2507: ?? operator requires Result<T>, Maybe<T>, or result-like enum
            er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=str(inner_type))
            return

        # Check for Result-like pattern: Ok(value) and Err(...)
        ok_variant = inner_type.get_variant("Ok")
        err_variant = inner_type.get_variant("Err")
        is_result_like = (ok_variant and err_variant and
                         len(ok_variant.associated_types) == 1)

        # Check for Maybe-like pattern: Some(value) and None()
        some_variant = inner_type.get_variant("Some")
        none_variant = inner_type.get_variant("None")
        is_maybe_like = (some_variant and none_variant and
                        len(some_variant.associated_types) == 1 and
                        len(none_variant.associated_types) == 0)

        if not is_result_like and not is_maybe_like:
            # Not a supported enum pattern
            er.emit(validator.reporter, er.ERR.CE2507, expr.loc, got=str(inner_type))
            return

    # Check if enclosing function returns a result-like enum
    if validator.current_function is None:
        # Not inside a function - this shouldn't happen but be defensive
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # CW2511: Warn about ?? operator in main function
    # While it works, explicit error handling is clearer at the program entry point
    if validator.current_function.name == "main":
        er.emit(validator.reporter, er.ERR.CW2511, expr.loc)
        # Continue validation - this is just a warning

    # Get the function's return type
    func_return_type = validator.current_function.ret

    if func_return_type is None:
        # Function has no return type
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # Check if function returns a result-like enum (Result<T> or concrete result-like enum)
    # Note: When ?? is used with Maybe<T>, it still propagates as Result.Err()
    # First try the monomorphized Result<T> pattern
    result_enum_name = f"Result<{func_return_type}>"
    if result_enum_name in validator.enum_table.by_name:
        # Function returns Result<T> - this is valid
        return

    # Otherwise, check if func_return_type itself is a result-like enum (e.g., FileResult)
    if isinstance(func_return_type, EnumType):
        # Check for Ok and Err variants
        ok_variant = func_return_type.get_variant("Ok")
        err_variant = func_return_type.get_variant("Err")

        if ok_variant and err_variant and len(ok_variant.associated_types) == 1:
            # Function returns a result-like enum - this is valid
            return

    # Function doesn't return a result-like enum
    er.emit(validator.reporter, er.ERR.CE2508, expr.loc)


def validate_bitwise_operation(validator: 'TypeValidator', expr: BinaryOp) -> None:
    """Validate that bitwise operators are used with numeric types only."""
    left_type = validator.infer_expression_type(expr.left)
    right_type = validator.infer_expression_type(expr.right)

    # Check left operand
    if left_type is not None and not is_numeric_type(left_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.left.loc, op=expr.op)
        return

    # Check right operand
    if right_type is not None and not is_numeric_type(right_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.right.loc, op=expr.op)
        return


def validate_bitwise_unary(validator: 'TypeValidator', expr: UnaryOp) -> None:
    """Validate that bitwise NOT (~) is used with numeric types only."""
    operand_type = validator.infer_expression_type(expr.expr)

    # Check operand
    if operand_type is not None and not is_numeric_type(operand_type):
        er.emit(validator.reporter, er.ERR.CE2004, expr.expr.loc, op=expr.op)


def validate_boolean_condition(validator: 'TypeValidator', expr: Expr, context: str) -> None:
    """Validate that an expression is boolean or Result<T> for control flow.

    Allows:
    - bool type (traditional boolean)
    - Result<T> enum (checks if Ok variant)
    """
    # First, validate the expression itself (this triggers visitor validation)
    validator.validate_expression(expr)

    # Then check if the result type is valid for a condition
    expr_type = validator.infer_expression_type(expr)
    if expr_type is not None:
        # Allow bool type
        if expr_type == BuiltinType.BOOL:
            return

        # Allow Result<T> enum types (check if Ok)
        if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
            return

        # All other types are invalid
        er.emit(validator.reporter, er.ERR.CE2005, expr.loc)
