"""Validation and pure helpers for the built-in Own<T> methods."""
from types import MappingProxyType
from typing import Any, Mapping, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.ownership import is_own_type
from sushi_lang.semantics.typesys import ReferenceType, StructType, Type, PointerType
from sushi_lang.internals.errors import raise_internal_error


#: Every built-in `Own@(T)` method and the number of arguments it takes. Each one is
#: checked by its count alone (CE2009, in the typecheck pass).
OWN_METHOD_ARITY: Mapping[str, int] = MappingProxyType({
    "alloc": 1, "get": 0, "destroy": 0, "clone": 0,
})


def is_builtin_own_method(method_name: str) -> bool:
    """Check if a method name is a builtin Own<T> method."""
    return method_name in OWN_METHOD_ARITY


def validate_own_method_with_validator(
    call: MethodCall,
    own_type: StructType,
    reporter: Any,
    validator: Any
) -> None:
    """Validate an Own<T> method call whose count is correct. The count is the whole rule."""
    if call.method not in OWN_METHOD_ARITY:
        raise_internal_error("CE0080", method=call.method)


def get_own_element_type(own_type: StructType) -> Type:
    """Extract element type T from Own<T> struct type."""
    value_field_type = own_type.fields[0][1]  # First field, second element is type

    if isinstance(value_field_type, PointerType):
        return value_field_type.pointee_type
    else:
        raise_internal_error("CE0081", type=str(value_field_type))


def own_payload_type(ty: Type) -> Optional[Type]:
    """The `T` of an `Own@(T)`, in every spelling, or None when there is none to read.

    ONE reader, because the pattern rule asks twice -- once to refuse a pattern and once
    to bind its names -- and two readers is how the two could disagree. A
    `GenericTypeRef` still carries the argument; an interned `StructType` carries the
    pointer field the argument was lowered into, and the field is what is read. The
    interned NAME is never sliced: type identity is nominal, and a name is looked up and
    not rebuilt (docs/design/type-identity.md).
    """
    if isinstance(ty, ReferenceType):
        ty = ty.referenced_type
    if not is_own_type(ty):
        return None
    if isinstance(ty, GenericTypeRef):
        return ty.type_args[0] if len(ty.type_args) == 1 else None
    if isinstance(ty, StructType):
        try:
            return get_own_element_type(ty)
        except (TypeError, IndexError):
            return None
    return None
