"""Validation and pure helpers for the built-in Own<T> methods."""
from typing import Any

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type, PointerType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error


def is_builtin_own_method(method_name: str) -> bool:
    """Check if a method name is a builtin Own<T> method."""
    return method_name in ("alloc", "get", "destroy", "clone")


def validate_own_method_with_validator(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T> method calls."""
    if call.method == "alloc":
        _validate_own_alloc(call, own_type, reporter, validator)
    elif call.method == "get":
        _validate_own_get(call, own_type, reporter)
    elif call.method == "destroy":
        _validate_own_destroy(call, own_type, reporter)
    elif call.method == "clone":
        _validate_own_clone(call, own_type, reporter)
    else:
        raise_internal_error("CE0080", method=call.method)


def _validate_own_alloc(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate Own<T>.alloc(value) method call."""
    if len(call.args) != 1:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="alloc", expected=1, got=len(call.args))


def _validate_own_get(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.get() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="get", expected=0, got=len(call.args))


def _validate_own_destroy(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.destroy() method call."""
    if len(call.args) != 0:
        er.emit(reporter, er.ERR.CE2016, call.loc,
               method="destroy", expected=0, got=len(call.args))


def _validate_own_clone(
    call: MethodCall,
    own_type: StructType,
    reporter: Any
) -> None:
    """Validate Own<T>.clone() -- arity 0, returns a new Own<T> over a copied payload."""
    if call.args:
        er.emit(reporter, er.ERR.CE2016, call.loc,
                method="clone", expected=0, got=len(call.args))


def get_own_element_type(own_type: StructType) -> Type:
    """Extract element type T from Own<T> struct type."""
    value_field_type = own_type.fields[0][1]  # First field, second element is type

    if isinstance(value_field_type, PointerType):
        return value_field_type.pointee_type
    else:
        raise_internal_error("CE0081", type=str(value_field_type))
