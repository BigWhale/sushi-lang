"""Validation and pure helpers for the built-in Own<T> methods.

The ir-free half of the former ``backend/generics/own.py``: method recognition,
Pass-2 argument validation, and the ``Own<T>`` element-type accessor. LLVM
emission (alloc/get/destroy) stays in the backend module.
"""
from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type, PointerType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def is_builtin_own_method(method_name: str) -> bool:
    """Check if a method name is a builtin Own<T> method.

    Args:
        method_name: The name of the method to check.

    Returns:
        True if this is a recognized Own<T> method, False otherwise.
    """
    return method_name in ("alloc", "get", "destroy")


def validate_own_method_with_validator(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T> method calls.

    Routes to specific validation functions based on method name.

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type (after monomorphization).
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    if call.method == "alloc":
        _validate_own_alloc(call, own_type, reporter, validator)
    elif call.method == "get":
        _validate_own_get(call, own_type, reporter)
    elif call.method == "destroy":
        _validate_own_destroy(call, own_type, reporter)
    else:
        # Unknown method - should not happen if is_builtin_own_method was called first
        raise_internal_error("CE0080", method=call.method)


def _validate_own_alloc(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T>.alloc(value) method call.

    Validates that exactly 1 argument is provided.
    Type checking of the argument will be done by the validator.

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
        validator: Type validator for inferring expression types.
    """
    # Validate argument count
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="alloc", expected=1, got=len(call.args))


def _validate_own_get(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.get() method call.

    Validates that no arguments are provided.
    Yields the payload as a non-owning borrow (never a second RAII owner).

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="get", expected=0, got=len(call.args))


def _validate_own_destroy(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.destroy() method call.

    Validates that no arguments are provided.
    Returns ~ (blank/void).

    Args:
        call: The method call AST node.
        own_type: The Own<T> struct type.
        reporter: Error reporter for emitting validation errors.
    """
    # Validate argument count
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="destroy", expected=0, got=len(call.args))


def get_own_element_type(own_type: StructType) -> Type:
    """Extract element type T from Own<T> struct type.

    Args:
        own_type: The Own<T> struct type (has field "value" of type T*).

    Returns:
        The element type T.
    """
    # Get the "value" field type which is T* (PointerType)
    value_field_type = own_type.fields[0][1]  # First field, second element is type

    # Extract pointee type from PointerType
    if isinstance(value_field_type, PointerType):
        return value_field_type.pointee_type
    else:
        # Fallback: should not happen if Own<T> is properly registered
        raise_internal_error("CE0081", type=str(value_field_type))
