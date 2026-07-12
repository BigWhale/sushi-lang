"""Result<T, E> and Maybe<T> method errors (CE25xx).

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.
"""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Result<T> method errors (CE25xx)
_add(ErrorMessage("CE2502", Severity.ERROR,
    "realise() requires exactly 1 argument, got {got}",
    Category.TYPE, "The realise() method on Result<T> must be called with exactly one default value argument."))

_add(ErrorMessage("CE2503", Severity.ERROR,
    "realise() default type mismatch: expected '{expected}', got '{got}'",
    Category.TYPE, "The default value type passed to realise() must match the T type in Result<T>."))

_add(ErrorMessage("CE2505", Severity.ERROR,
    "cannot assign Result<T> to non-Result variable without handling (use .realise() or pattern matching)",
    Category.TYPE, "Result<T> values must be explicitly handled before assigning to non-Result variables."))

_add(ErrorMessage("CE2506", Severity.ERROR,
    "cannot call .realise() on Result<~> (blank type has no value to extract)",
    Category.TYPE, "Blank functions return Result<~> which has no meaningful value. Use if statement to check success/failure instead."))

# Try operator (??) errors (CE25xx continued)
_add(ErrorMessage("CE2507", Severity.ERROR,
    "?? operator requires Result<T>, Maybe<T>, or result-like enum (with Ok/Err or Some/None variants), got '{got}'",
    Category.TYPE, "The ?? operator requires an enum with Ok/Err variants (e.g., Result<T>, FileResult) or Some/None variants (e.g., Maybe<T>)."))

_add(ErrorMessage("CE2508", Severity.ERROR,
    "?? operator can only be used in functions returning a result-like enum (with Ok/Err variants)",
    Category.TYPE, "The ?? operator propagates errors by early return, so it requires the enclosing function to return a result-like enum (e.g., Result<T>, FileResult). Note: Maybe<T> can be used with ??, but it propagates as Result.Err()."))

_add(ErrorMessage("CE2509", Severity.ERROR,
    "operator '+' cannot be used with string types (use string interpolation instead: \"text {{variable}}\")",
    Category.TYPE, "Sushi does not support string concatenation with the + operator. Use string interpolation for combining strings."))

_add(ErrorMessage("CE2510", Severity.ERROR,
    "cannot use operator with mixed numeric types: {left_type} and {right_type} (use 'as' to explicitly cast one operand)",
    Category.TYPE, "Sushi does not allow implicit numeric type conversions in comparisons or arithmetic operations. Use the 'as' keyword to explicitly cast one operand to match the other's type. Example: if (x == 3.14 as f32) { ... }"))

_add(ErrorMessage("CE2511", Severity.ERROR,
    "error type mismatch in propagation: cannot propagate Result<{ok_type}, {inner_err}> to function returning Result<{ok_type}, {outer_err}>",
    Category.TYPE, "The ?? operator requires error types to match exactly. Inner function returns Result<T, {inner_err}> but outer function returns Result<T, {outer_err}>. Error type conversion is not supported yet."))
