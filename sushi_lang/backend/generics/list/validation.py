"""
Validation for List<T> methods.

This module provides validation functions to check if method calls on List<T>
are valid and have the correct number/types of arguments.
"""

from typing import Any, Callable
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
import sushi_lang.internals.errors as er
from sushi_lang.internals.errors import raise_internal_error


# List of all supported List<T> methods
BUILTIN_LIST_METHODS = {
    "new",           # List.new() -> List<T>
    "with_capacity", # List.with_capacity(i32) -> List<T>
    "len",           # list.len() -> i32
    "capacity",      # list.capacity() -> i32
    "is_empty",      # list.is_empty() -> bool
    "push",          # list.push(T) -> ~
    "pop",           # list.pop() -> Maybe<T>
    "get",           # list.get(i32) -> Maybe<T>
    "clear",         # list.clear() -> ~
    "reserve",       # list.reserve(i32) -> ~
    "shrink_to_fit", # list.shrink_to_fit() -> ~
    "insert",        # list.insert(i32, T) -> Result<~>
    "remove",        # list.remove(i32) -> Maybe<T>
    "destroy",       # list.destroy() -> ~
    "free",          # list.free() -> ~
    "debug",         # list.debug() -> ~
    "iter",          # list.iter() -> Iterator<T>
}


def is_builtin_list_method(method_name: str) -> bool:
    """Check if a method name is a built-in List<T> method.

    Args:
        method_name: The method name to check.

    Returns:
        True if this is a List<T> method, False otherwise.
    """
    return method_name in BUILTIN_LIST_METHODS


def validate_list_method_with_validator(
    call: MethodCall,
    list_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate List<T> method calls.

    Routes to specific validation based on method name and checks argument counts.

    Args:
        call: The method call AST node.
        list_type: The List<T> struct type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    method = call.method
    num_args = len(call.args)

    # Define expected argument counts for each method
    expected_args = {
        # 0 arguments
        "new": 0, "len": 0, "capacity": 0, "is_empty": 0,
        "pop": 0, "clear": 0, "shrink_to_fit": 0, "destroy": 0, "free": 0, "debug": 0, "iter": 0,
        # 1 argument
        "with_capacity": 1, "push": 1, "get": 1, "reserve": 1, "remove": 1,
        # 2 arguments
        "insert": 2,
    }

    if method not in expected_args:
        raise_internal_error("CE0083", method=method)

    expected = expected_args[method]
    if num_args != expected:
        er.emit(reporter, er.ERR.CE2053, call.loc,
                method=method, expected=expected, got=num_args)


