"""Hashability analysis and hash() method registration."""
from __future__ import annotations

from typing import Any, Optional

from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    ForeignPtrType,
    StructType,
    Type,
    UnknownType,
)
from sushi_lang.semantics.generics.types import GenericEnumType, GenericStructType
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.sushi_stdlib.src.common import (
    BuiltinMethod,
    get_builtin_method,
    get_hash_emitter_factory,
    register_builtin_method,
)
from sushi_lang.semantics.generics.type_display import display_type


def can_struct_be_hashed(struct_type: StructType, visited: Optional[set] = None, path: Optional[list] = None) -> tuple[bool, str]:
    """Check if a struct type can have an auto-derived hash method."""
    if visited is None:
        visited = set()
    if path is None:
        path = []

    if struct_type.name in visited:
        return False, f"recursive struct type: {' -> '.join(path + [struct_type.name])}"

    visited.add(struct_type.name)
    path.append(struct_type.name)

    # Generic structs cannot be hashed (should be monomorphized first)
    if isinstance(struct_type, GenericStructType):
        return False, f"generic struct {struct_type.name} (should be monomorphized first)"

    for field_name, field_type in struct_type.fields:
        if isinstance(field_type, UnknownType):
            return False, f"field '{field_name}' has unresolved type '{field_type.name}'"

        if isinstance(field_type, ForeignPtrType):
            return False, f"field '{field_name}' is a foreign ptr (unhashable)"

        if isinstance(field_type, (ArrayType, DynamicArrayType)):
            can_hash, reason = can_array_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        if isinstance(field_type, EnumType):
            can_hash, reason = can_enum_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

        if isinstance(field_type, StructType):
            can_hash, reason = can_struct_be_hashed(field_type, visited.copy(), path.copy())
            if not can_hash:
                return False, f"field '{field_name}' -> {reason}"

    return True, "all fields are hashable"


def can_enum_be_hashed(enum_type: EnumType, visited: Optional[set] = None, path: Optional[list] = None) -> tuple[bool, str]:
    """Check if an enum type can have an auto-derived hash method."""
    if visited is None:
        visited = set()
    if path is None:
        path = []

    if enum_type.name in visited:
        return False, f"recursive enum type: {' -> '.join(path + [enum_type.name])}"

    visited.add(enum_type.name)
    path.append(enum_type.name)

    # Generic enums cannot be hashed (should be monomorphized first)
    if isinstance(enum_type, GenericEnumType):
        return False, f"generic enum {enum_type.name} (should be monomorphized first)"

    for variant in enum_type.variants:
        for _assoc_idx, assoc_type in enumerate(variant.associated_types):
            if isinstance(assoc_type, UnknownType):
                return False, f"variant {variant.name} has unresolved type '{assoc_type.name}'"

            if isinstance(assoc_type, ForeignPtrType):
                return False, f"variant {variant.name} carries a foreign ptr (unhashable)"

            if isinstance(assoc_type, (ArrayType, DynamicArrayType)):
                can_hash, reason = can_array_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            if isinstance(assoc_type, EnumType):
                can_hash, reason = can_enum_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

            if isinstance(assoc_type, StructType):
                can_hash, reason = can_struct_be_hashed(assoc_type, visited.copy(), path.copy())
                if not can_hash:
                    return False, f"variant {variant.name} -> {reason}"

    return True, "all variant types are hashable"


def can_array_be_hashed(array_type: Type, visited: Optional[set] = None, path: Optional[list] = None) -> tuple[bool, str]:
    """Check if an array type can have an auto-derived hash method."""
    if not isinstance(array_type, (ArrayType, DynamicArrayType)):
        return False, f"not an array type: {type(array_type).__name__}"

    element_type = array_type.base_type

    if visited is None:
        visited = set()
    if path is None:
        path = []

    if isinstance(element_type, (ArrayType, DynamicArrayType)):
        return False, "nested array type (arrays of arrays not supported)"

    if isinstance(element_type, BuiltinType):
        return True, "element type is primitive"

    if isinstance(element_type, StructType):
        can_hash, reason = can_struct_be_hashed(element_type, visited.copy(), path.copy())
        if not can_hash:
            return False, f"element struct type cannot be hashed: {reason}"
        return True, "element struct type is hashable"

    if isinstance(element_type, EnumType):
        can_hash, reason = can_enum_be_hashed(element_type, visited.copy(), path.copy())
        if not can_hash:
            return False, f"element enum type cannot be hashed: {reason}"
        return True, "element enum type is hashable"

    return False, f"element type {element_type} is not hashable"


def _validate_struct_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on struct types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))


def _validate_enum_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on enum types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))


def _validate_array_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on array types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
                name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))

    if isinstance(target_type, (ArrayType, DynamicArrayType)):
        element_type = target_type.base_type
        if isinstance(element_type, (ArrayType, DynamicArrayType)):
            er.emit(reporter, er.ERR.CE2051, call.loc,
                    message="cannot hash array of arrays (nested arrays not supported)")


def _lazy_hash_emitter(kind: str, target_type: Type):
    """Build a hash() emitter that resolves its backend factory on first emission."""
    def emit(codegen, call, receiver_value, receiver_type, to_i1):
        factory = get_hash_emitter_factory(kind)
        if factory is None:
            raise_internal_error("CE0123", kind=kind)
        return factory(target_type)(codegen, call, receiver_value, receiver_type, to_i1)

    return emit


def _register_hash_method(target_type: Type, kind: str, validator, description: str) -> None:
    """Register the auto-derived hash() method for a type."""
    if get_builtin_method(target_type, "hash") is not None:
        return  # Already registered

    register_builtin_method(
        target_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=description,
            semantic_validator=validator,
            llvm_emitter=_lazy_hash_emitter(kind, target_type),
        )
    )


def register_struct_hash_method(struct_type: StructType) -> None:
    """Register the auto-derived hash() method for a hashable struct type."""
    can_hash, _reason = can_struct_be_hashed(struct_type)
    if not can_hash:
        return

    _register_hash_method(
        struct_type, "struct", _validate_struct_hash,
        f"Auto-derived hash for struct {struct_type}",
    )


def register_enum_hash_method(enum_type: EnumType) -> None:
    """Register the auto-derived hash() method for a hashable enum type."""
    can_hash, _reason = can_enum_be_hashed(enum_type)
    if not can_hash:
        return

    _register_hash_method(
        enum_type, "enum", _validate_enum_hash,
        f"Auto-derived hash for enum {enum_type}",
    )


def register_array_hash_method(array_type: Type) -> None:
    """Register the auto-derived hash() method for a hashable array type."""
    can_hash, _reason = can_array_be_hashed(array_type)
    if not can_hash:
        return

    _register_hash_method(
        array_type, "array", _validate_array_hash,
        f"Auto-derived hash for array {array_type}",
    )
