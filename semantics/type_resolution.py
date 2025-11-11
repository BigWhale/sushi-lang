# semantics/type_resolution.py
"""
Type resolution utilities for UnknownType to StructType/EnumType conversion.

This module centralizes the logic for resolving UnknownType instances to their
concrete types (StructType or EnumType) using struct and enum tables. This pattern
appears in multiple places across the semantic analysis passes and is now unified here.

Key Functions:
- resolve_unknown_type(): Resolve a single UnknownType to StructType/EnumType
- resolve_type_recursively(): Recursively resolve UnknownType in nested types (arrays, etc.)
"""
from __future__ import annotations
from typing import Dict, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from semantics.typesys import Type, StructType, EnumType


def resolve_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Resolve UnknownType or GenericTypeRef to StructType or EnumType if possible.

    If the UnknownType name matches a struct in the struct table or an enum
    in the enum table, return the corresponding concrete type.

    If the type is a GenericTypeRef, generate the concrete type name
    (e.g., "Box<i32>") and look it up in the struct/enum tables.

    Args:
        ty: The type to resolve (may be UnknownType, GenericTypeRef, or any other Type)
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances

    Returns:
        The resolved type (StructType, EnumType, or original type)

    Examples:
        >>> # Assuming Point is a known struct
        >>> unknown = UnknownType(name="Point")
        >>> resolved = resolve_unknown_type(unknown, struct_table, enum_table)
        >>> isinstance(resolved, StructType)  # True

        >>> # Assuming Box<i32> is a monomorphized generic struct
        >>> generic_ref = GenericTypeRef(base_name="Box", type_args=(BuiltinType.I32,))
        >>> resolved = resolve_unknown_type(generic_ref, struct_table, enum_table)
        >>> isinstance(resolved, StructType)  # True
    """
    from semantics.typesys import UnknownType
    from semantics.generics.types import GenericTypeRef

    if isinstance(ty, UnknownType):
        # Check if this unknown type is actually a known struct
        if ty.name in struct_table:
            return struct_table[ty.name]
        # Check if this unknown type is actually a known enum
        if ty.name in enum_table:
            return enum_table[ty.name]

    # Handle GenericTypeRef - resolve to concrete monomorphized type
    elif isinstance(ty, GenericTypeRef):
        # Generate the concrete type name (e.g., "Box<i32>")
        type_arg_strs = ", ".join(str(arg) for arg in ty.type_args)
        concrete_name = f"{ty.base_name}<{type_arg_strs}>"

        # Look up the monomorphized concrete type
        if concrete_name in struct_table:
            return struct_table[concrete_name]
        if concrete_name in enum_table:
            return enum_table[concrete_name]

    return ty


def resolve_type_recursively(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType']
) -> 'Type':
    """Recursively resolve UnknownType in nested type structures.

    Handles resolution for:
    - Simple UnknownType (delegates to resolve_unknown_type)
    - ArrayType with UnknownType base_type
    - DynamicArrayType with UnknownType base_type
    - GenericTypeRef with UnknownType in type_args
    - StructType with UnknownType in field types
    - EnumType with UnknownType in variant associated_types

    Args:
        ty: The type to resolve (may contain nested UnknownType instances)
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances

    Returns:
        The resolved type with all nested UnknownType instances resolved

    Examples:
        >>> # Array of unknown type Point[] -> Point is a struct
        >>> unknown_array = DynamicArrayType(base_type=UnknownType(name="Point"))
        >>> resolved = resolve_type_recursively(unknown_array, struct_table, enum_table)
        >>> isinstance(resolved.base_type, StructType)  # True
    """
    from semantics.typesys import (
        UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
    )
    from semantics.generics.types import GenericTypeRef

    # First, try to resolve the type itself if it's UnknownType
    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    # Handle array types with recursive base type resolution
    if isinstance(resolved_ty, ArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table
        )
        if resolved_base != resolved_ty.base_type:
            return ArrayType(base_type=resolved_base, size=resolved_ty.size)

    elif isinstance(resolved_ty, DynamicArrayType):
        resolved_base = resolve_type_recursively(
            resolved_ty.base_type, struct_table, enum_table
        )
        if resolved_base != resolved_ty.base_type:
            return DynamicArrayType(base_type=resolved_base)

    # Handle generic type references with recursive type argument resolution
    elif isinstance(resolved_ty, GenericTypeRef):
        resolved_args = tuple(
            resolve_type_recursively(arg, struct_table, enum_table)
            for arg in resolved_ty.type_args
        )
        if resolved_args != resolved_ty.type_args:
            return GenericTypeRef(
                base_name=resolved_ty.base_name,
                type_args=resolved_args
            )

    # Handle struct types with recursive field type resolution
    elif isinstance(resolved_ty, StructType):
        resolved_fields = []
        has_changes = False
        for field_name, field_type in resolved_ty.fields:
            resolved_field_type = resolve_type_recursively(
                field_type, struct_table, enum_table
            )
            resolved_fields.append((field_name, resolved_field_type))
            if resolved_field_type != field_type:
                has_changes = True

        if has_changes:
            return StructType(
                name=resolved_ty.name,
                fields=resolved_fields
            )

    # Handle enum types with recursive variant associated type resolution
    elif isinstance(resolved_ty, EnumType):
        # Note: EnumType resolution is more complex and may need
        # to create new EnumVariant instances. For now, we return
        # the type unchanged and let the caller handle this case.
        # Full recursive enum resolution can be added if needed.
        pass

    return resolved_ty


def contains_unresolvable_unknown_type(
    ty: 'Type',
    struct_table: Dict[str, 'StructType'],
    enum_table: Dict[str, 'EnumType'],
    visited: Optional[Set[str]] = None
) -> bool:
    """Check if a type contains UnknownType that cannot be resolved.

    Recursively checks for UnknownType instances in the type structure
    and attempts to resolve them. Returns True only if there are UnknownType
    instances that cannot be resolved to StructType or EnumType.

    Handles recursive types (e.g., Own<Expr> in enum Expr) by tracking visited types.

    Args:
        ty: The type to check
        struct_table: Dictionary mapping struct names to StructType instances
        enum_table: Dictionary mapping enum names to EnumType instances
        visited: Set of visited type names to prevent infinite recursion

    Returns:
        True if there are unresolvable UnknownType instances, False otherwise

    Examples:
        >>> # MyStruct is known, but OtherType is not
        >>> arr_type = DynamicArrayType(base_type=UnknownType(name="OtherType"))
        >>> contains_unresolvable_unknown_type(arr_type, struct_table, enum_table)
        True
    """
    from semantics.typesys import (
        UnknownType, ArrayType, DynamicArrayType, StructType, EnumType
    )
    from semantics.generics.types import GenericTypeRef

    # Initialize visited set on first call
    if visited is None:
        visited = set()

    # Try to resolve the type
    resolved_ty = resolve_unknown_type(ty, struct_table, enum_table)

    # If still UnknownType after resolution, it's unresolvable
    if isinstance(resolved_ty, UnknownType):
        return True

    # Create a unique key for this type to detect cycles
    type_key = None
    if isinstance(resolved_ty, StructType):
        type_key = f"struct:{resolved_ty.name}"
    elif isinstance(resolved_ty, EnumType):
        type_key = f"enum:{resolved_ty.name}"

    # Check for cycles - if we've seen this type before, assume it's resolvable
    if type_key and type_key in visited:
        return False

    # Mark this type as visited
    if type_key:
        visited = visited | {type_key}

    # Recursively check nested types
    if isinstance(resolved_ty, (ArrayType, DynamicArrayType)):
        return contains_unresolvable_unknown_type(
            resolved_ty.base_type, struct_table, enum_table, visited
        )

    elif isinstance(resolved_ty, GenericTypeRef):
        return any(
            contains_unresolvable_unknown_type(arg, struct_table, enum_table, visited)
            for arg in resolved_ty.type_args
        )

    elif isinstance(resolved_ty, StructType):
        return any(
            contains_unresolvable_unknown_type(field_type, struct_table, enum_table, visited)
            for _, field_type in resolved_ty.fields
        )

    elif isinstance(resolved_ty, EnumType):
        return any(
            contains_unresolvable_unknown_type(assoc_type, struct_table, enum_table, visited)
            for variant in resolved_ty.variants
            for assoc_type in variant.associated_types
        )

    return False
