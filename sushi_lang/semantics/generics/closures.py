"""Validation and pure helpers for the built-in methods on a function value."""
from __future__ import annotations

from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import FunctionType, Type
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.generics.type_display import display_type


def is_builtin_function_method(method_name: str) -> bool:
    """Is `method_name` a built-in method on a function value?"""
    return method_name == "clone"


def function_method_return_type(method_name: str, fn_type: FunctionType) -> Type | None:
    """Return type of a built-in function-value method, or None if there is no such pair."""
    if method_name == "clone":
        return fn_type
    return None


def validate_function_method_with_validator(
    call: MethodCall,
    fn_type: FunctionType,
    reporter: Any,
    validator: Any,
) -> None:
    """Validate a built-in function-value method call."""
    if call.method == "clone":
        _validate_function_clone(call, fn_type, reporter)
    else:
        # Unreachable if is_builtin_function_method was consulted first.
        raise_internal_error("CE0080", method=call.method)


def _validate_function_clone(call: MethodCall, fn_type: FunctionType, reporter: Any) -> None:
    """clone() takes no arguments, like every other clone in the language."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(fn_type)}.clone", expected=0, got=len(call.args))
