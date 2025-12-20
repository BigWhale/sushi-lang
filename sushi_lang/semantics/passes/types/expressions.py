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

from sushi_lang.internals import errors as er
from sushi_lang.semantics.typesys import BuiltinType, ArrayType, DynamicArrayType, EnumType, ResultType
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ast import ArrayLiteral, IndexAccess, CastExpr, TryExpr, BinaryOp, UnaryOp, Expr, IntLit, RangeExpr
from sushi_lang.semantics.type_predicates import is_numeric_type
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


def validate_range_expression(validator: 'TypeValidator', expr: 'RangeExpr') -> None:
    """Validate range expression - start and end must be integer types.

    Args:
        validator: The TypeValidator instance.
        expr: The range expression to validate.
    """
    # Validate start expression
    validator.validate_expression(expr.start)
    start_type = validator.infer_expression_type(expr.start)

    # Validate end expression
    validator.validate_expression(expr.end)
    end_type = validator.infer_expression_type(expr.end)

    # Check that start is integer type (i8, i16, i32, i64, u8, u16, u32, u64)
    if start_type is not None and not is_numeric_type(start_type):
        er.emit(validator.reporter, er.ERR.CE2072, expr.start.loc,
               got=str(start_type), expected="integer type")

    # Check that end is integer type
    if end_type is not None and not is_numeric_type(end_type):
        er.emit(validator.reporter, er.ERR.CE2072, expr.end.loc,
               got=str(end_type), expected="integer type")

    # Note: We accept any integer type (i8, i16, i32, i64, u8, u16, u32, u64)
    # but the backend will cast to i32 for iteration. Type compatibility
    # checking happens during cast emission.


def validate_try_expression(validator: 'TypeValidator', expr: 'TryExpr') -> None:
    """Validate ?? operator usage and annotate AST with inferred types.

    Validates that:
    1. The inner expression is Result<T>, Maybe<T>, or another result-like enum (CE2507)
    2. The enclosing function returns Result<T> or another result-like enum (CE2508)

    Supported enum patterns:
    - Result-like: Ok(value) and Err(...) variants (e.g., Result<T>, FileResult)
    - Maybe-like: Some(value) and None() variants (e.g., Maybe<T>)

    When ?? is applied to Maybe<T>:
    - Some(value) unwraps to value
    - None() propagates as Result.Err() to the enclosing function

    After validation, annotates the TryExpr AST node with:
    - inferred_inner_type: The EnumType of the inner expression
    - inferred_unwrapped_type: The success type T
    - inferred_success_tag: Variant index for Ok/Some
    - inferred_error_type: The error type E (None for Maybe-like)
    - inferred_error_tag: Variant index for Err (None for Maybe-like)
    - inferred_func_return_type: The enclosing function's ResultType

    Args:
        validator: The TypeValidator instance.
        expr: The TryExpr node to validate and annotate.
    """
    # First validate the inner expression
    validator.validate_expression(expr.expr)

    # Get the type of the inner expression
    inner_type = validator.infer_expression_type(expr.expr)

    # Variables for AST annotation
    unwrapped_type = None
    success_tag = None
    error_type = None
    error_tag = None

    # Check if inner expression is a supported type (Result<T, E>, Maybe<T>, or result-like enum)
    if inner_type is not None:
        # ResultType is always valid for ??
        if isinstance(inner_type, ResultType):
            # ResultType is a semantic type - convert to EnumType for backend
            # The enum table should have the corresponding Result<T, E> enum
            result_enum_name = f"Result<{inner_type.ok_type}, {inner_type.err_type}>"
            if result_enum_name in validator.enum_table.by_name:
                inner_type = validator.enum_table.by_name[result_enum_name]
            # Extract variant info from ResultType
            if isinstance(inner_type, EnumType):
                ok_variant = inner_type.get_variant("Ok")
                if ok_variant and ok_variant.associated_types:
                    unwrapped_type = ok_variant.associated_types[0]
                    success_tag = inner_type.get_variant_index("Ok")
                err_variant = inner_type.get_variant("Err")
                if err_variant and err_variant.associated_types:
                    error_type = err_variant.associated_types[0]
                    error_tag = inner_type.get_variant_index("Err")
        elif isinstance(inner_type, EnumType):
            # For EnumType, check if it matches Result-like or Maybe-like pattern
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

            # Extract variant info for annotation
            if is_result_like:
                unwrapped_type = ok_variant.associated_types[0]
                success_tag = inner_type.get_variant_index("Ok")
                if err_variant.associated_types:
                    error_type = err_variant.associated_types[0]
                error_tag = inner_type.get_variant_index("Err")
            else:  # is_maybe_like
                unwrapped_type = some_variant.associated_types[0]
                success_tag = inner_type.get_variant_index("Some")
                # Maybe-like has no error variant with data
                error_type = None
                error_tag = None
        else:
            # Not an enum, ResultType, or MaybeType
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
    # For implicit syntax (fn foo() i32 | MyError), we need to construct ResultType
    func_return_type = validator.current_function.ret

    if func_return_type is None:
        # Function has no return type
        er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
        return

    # If function uses implicit Result syntax (not explicitly Result<T, E>),
    # construct the ResultType for validation
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.type_resolution import TypeResolver
    if not isinstance(func_return_type, ResultType) and not (isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result"):
        # Implicit syntax: fn foo() i32 | MyError or fn foo() i32 (defaults to StdError)
        # Construct ResultType(ok_type=i32, err_type=MyError or StdError)
        if validator.current_function.err_type is not None:
            # Custom error type: fn foo() i32 | MyError
            resolver = TypeResolver(
                validator.struct_table.by_name,
                validator.enum_table.by_name
            )
            err_type_resolved = resolver.resolve(validator.current_function.err_type)
        else:
            # Default to StdError: fn foo() i32 or fn main() i32
            err_type_resolved = validator.enum_table.by_name.get("StdError")
            if err_type_resolved is None:
                # StdError not found (shouldn't happen)
                er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
                return
        func_return_type = ResultType(ok_type=func_return_type, err_type=err_type_resolved)
    elif isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result":
        # Explicit Result<T, E> syntax - resolve to ResultType
        resolver = TypeResolver(
            validator.struct_table.by_name,
            validator.enum_table.by_name
        )
        func_return_type = resolver.resolve(func_return_type)

    # Check if function returns Result<T, E>
    # Note: When ?? is used with Maybe<T>, it still propagates as Result.Err()

    # ResultType is always valid
    if isinstance(func_return_type, ResultType):
        # Function returns Result<T, E> - validate error types match
        inner_err_type = None
        outer_err_type = func_return_type.err_type

        # Extract inner error type
        if isinstance(inner_type, ResultType):
            inner_err_type = inner_type.err_type
        elif isinstance(inner_type, EnumType):
            err_variant = inner_type.get_variant("Err")
            if err_variant and err_variant.associated_types:
                inner_err_type = err_variant.associated_types[0]

        # Strict error type matching - no conversions
        # Compare by string representation since types might be different instances
        if inner_err_type is not None and outer_err_type is not None:
            if str(inner_err_type) != str(outer_err_type):
                # Error type mismatch - emit CE2511
                ok_type_str = str(func_return_type.ok_type) if hasattr(func_return_type, 'ok_type') else "T"
                er.emit(validator.reporter, er.ERR.CE2511, expr.loc,
                        ok_type=ok_type_str,
                        inner_err=str(inner_err_type),
                        outer_err=str(outer_err_type))
                return

        # Validation passed - annotate AST
        _annotate_try_expr(expr, inner_type, unwrapped_type, success_tag,
                          error_type, error_tag, func_return_type)
        return

    # GenericTypeRef with base_name "Result" is also valid (not yet resolved)
    if isinstance(func_return_type, GenericTypeRef) and func_return_type.base_name == "Result":
        # Verify it has exactly 2 type parameters
        if len(func_return_type.type_args) != 2:
            er.emit(validator.reporter, er.ERR.CE2508, expr.loc)
            return

        # Extract error types for validation
        inner_err_type = None
        outer_err_type = func_return_type.type_args[1]

        # Extract inner error type
        if isinstance(inner_type, ResultType):
            inner_err_type = inner_type.err_type
        elif isinstance(inner_type, EnumType):
            err_variant = inner_type.get_variant("Err")
            if err_variant and err_variant.associated_types:
                inner_err_type = err_variant.associated_types[0]

        # Strict error type matching
        # Compare by string representation since types might be different instances
        if inner_err_type is not None and outer_err_type is not None:
            if str(inner_err_type) != str(outer_err_type):
                # Error type mismatch - emit CE2511
                ok_type_str = str(func_return_type.type_args[0])
                er.emit(validator.reporter, er.ERR.CE2511, expr.loc,
                        ok_type=ok_type_str,
                        inner_err=str(inner_err_type),
                        outer_err=str(outer_err_type))
                return

        # Validation passed - annotate AST (convert GenericTypeRef to ResultType)
        resolved_func_return = ResultType(
            ok_type=func_return_type.type_args[0],
            err_type=func_return_type.type_args[1]
        )
        _annotate_try_expr(expr, inner_type, unwrapped_type, success_tag,
                          error_type, error_tag, resolved_func_return)
        return

    # Function doesn't return Result<T, E>
    er.emit(validator.reporter, er.ERR.CE2508, expr.loc)


def _annotate_try_expr(
    expr: 'TryExpr',
    inner_type: 'EnumType',
    unwrapped_type: 'Type',
    success_tag: int,
    error_type: 'Optional[Type]',
    error_tag: 'Optional[int]',
    func_return_type: 'ResultType'
) -> None:
    """Annotate TryExpr AST node with inferred type information.

    These annotations are used by the backend to emit code without
    re-inferring types, following the principle of separating semantic
    analysis from code generation.

    Args:
        expr: The TryExpr node to annotate.
        inner_type: The EnumType of the inner expression.
        unwrapped_type: The success type T.
        success_tag: Variant index for Ok/Some.
        error_type: The error type E (None for Maybe-like).
        error_tag: Variant index for Err (None for Maybe-like).
        func_return_type: The enclosing function's ResultType.
    """
    expr.inferred_inner_type = inner_type
    expr.inferred_unwrapped_type = unwrapped_type
    expr.inferred_success_tag = success_tag
    expr.inferred_error_type = error_type
    expr.inferred_error_tag = error_tag
    expr.inferred_func_return_type = func_return_type


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
    """Validate that an expression is boolean or Result<T, E> for control flow.

    Allows:
    - bool type (traditional boolean)
    - Result<T, E> in any representation (EnumType, ResultType, GenericTypeRef)
    """
    # First, validate the expression itself (this triggers visitor validation)
    validator.validate_expression(expr)

    # Then check if the result type is valid for a condition
    expr_type = validator.infer_expression_type(expr)
    if expr_type is not None:
        # Allow bool type
        if expr_type == BuiltinType.BOOL:
            return

        # Allow Result<T, E> enum types (monomorphized representation)
        if isinstance(expr_type, EnumType) and expr_type.name.startswith("Result<"):
            return

        # Allow ResultType (semantic representation)
        if isinstance(expr_type, ResultType):
            return

        # Allow GenericTypeRef("Result", ...) (parsed representation)
        from sushi_lang.semantics.generics.types import GenericTypeRef
        if isinstance(expr_type, GenericTypeRef) and expr_type.base_name == "Result":
            return

        # All other types are invalid
        er.emit(validator.reporter, er.ERR.CE2005, expr.loc)


def check_propagation_in_expression(expr: Expr) -> bool:
    """Check if expression contains ?? operator (TryExpr).

    Recursively traverses the expression tree to detect TryExpr nodes.
    Used for CW2511 warning in main() function.

    Args:
        expr: The expression to check

    Returns:
        True if expression contains ??, False otherwise
    """
    if isinstance(expr, TryExpr):
        return True

    # Recursively check child expressions
    from sushi_lang.semantics.ast import (
        BinaryOp, UnaryOp, Call, MethodCall, DotCall, IndexAccess, MemberAccess,
        ArrayLiteral, EnumConstructor, CastExpr, RangeExpr
    )

    if isinstance(expr, (BinaryOp, RangeExpr)):
        return (check_propagation_in_expression(expr.left) or
                check_propagation_in_expression(expr.right))

    elif isinstance(expr, UnaryOp):
        return check_propagation_in_expression(expr.expr)

    elif isinstance(expr, (Call, MethodCall, DotCall)):
        # Check arguments
        if hasattr(expr, 'args') and expr.args:
            return any(check_propagation_in_expression(arg) for arg in expr.args)

    elif isinstance(expr, IndexAccess):
        return (check_propagation_in_expression(expr.array) or
                check_propagation_in_expression(expr.index))

    elif isinstance(expr, MemberAccess):
        return check_propagation_in_expression(expr.receiver)

    elif isinstance(expr, ArrayLiteral):
        if expr.elements:
            return any(check_propagation_in_expression(elem) for elem in expr.elements)

    elif isinstance(expr, EnumConstructor):
        if expr.args:
            return any(check_propagation_in_expression(arg) for arg in expr.args)

    elif isinstance(expr, CastExpr):
        return check_propagation_in_expression(expr.expr)

    return False
