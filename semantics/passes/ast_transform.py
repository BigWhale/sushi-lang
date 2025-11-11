"""
Pass 1.7: AST Transformation and Type Resolution

This pass runs AFTER monomorphization (Pass 1.6) and performs:
1. Struct field type resolution (UnknownType → concrete types)
   - Resolves references to other structs, enums, and built-in types
   - Handles both generic and non-generic structs
2. EnumConstructor vs MethodCall ambiguity resolution (future)
3. Special identifier handling (stdin, stdout, stderr) (future)

This pass is critical for hash registration because:
- Nested struct fields must be resolved before we can determine hashability
- Generic structs need their type parameters substituted before resolution
"""

from typing import Dict, Set
from semantics.passes.collect import StructTable, EnumTable
from semantics.typesys import StructType, EnumType, UnknownType, Type, BuiltinType


def resolve_struct_field_types(
    struct_table: StructTable,
    enum_table: EnumTable
) -> None:
    """Resolve UnknownType references in struct fields to concrete types.

    After monomorphization, all struct types exist in the struct_table (including
    monomorphized generics like Pair<i32, string>). However, their field types
    may still be UnknownType references (e.g., Rectangle.top_left is UnknownType("Point")
    instead of StructType(Point)).

    This function resolves all such references by looking up types in the available tables.

    Args:
        struct_table: Table of all struct types (after monomorphization)
        enum_table: Table of all enum types (after monomorphization)
    """
    # Build a lookup table for all known types
    type_lookup: Dict[str, Type] = {}

    # Add all built-in types
    for builtin in BuiltinType:
        type_lookup[str(builtin).lower()] = builtin

    # Add all struct types
    for struct_name, struct_type in struct_table.by_name.items():
        type_lookup[struct_name] = struct_type

    # Add all enum types
    for enum_name, enum_type in enum_table.by_name.items():
        type_lookup[enum_name] = enum_type

    # Resolve field types for each struct
    # Use list() to avoid "dictionary changed size during iteration" errors
    for struct_name in list(struct_table.by_name.keys()):
        struct_type = struct_table.by_name[struct_name]

        if not isinstance(struct_type, StructType):
            continue  # Skip if not a regular StructType

        # Resolve each field type
        resolved_fields = []
        needs_update = False
        for field_name, field_type in struct_type.fields:
            resolved_type = _resolve_type(field_type, type_lookup)
            resolved_fields.append((field_name, resolved_type))
            # Check if any type was actually resolved
            if resolved_type is not field_type:
                needs_update = True

        # Only update if we actually resolved something
        if needs_update:
            # Update struct fields in-place (StructType is a dataclass, so we need to replace the whole tuple)
            # Since StructType is frozen (immutable), we need to use object.__setattr__
            object.__setattr__(struct_type, 'fields', tuple(resolved_fields))


def resolve_enum_variant_types(
    struct_table: StructTable,
    enum_table: EnumTable
) -> None:
    """Resolve UnknownType references in enum variant associated types to concrete types.

    Similar to resolve_struct_field_types, but for enum variants. After monomorphization,
    enum variant associated types may still be UnknownType references that need to be
    resolved to their concrete types.

    Args:
        struct_table: Table of all struct types (after monomorphization)
        enum_table: Table of all enum types (after monomorphization)
    """
    # Build a lookup table for all known types
    type_lookup: Dict[str, Type] = {}

    # Add all built-in types
    for builtin in BuiltinType:
        type_lookup[str(builtin).lower()] = builtin

    # Add all struct types
    for struct_name, struct_type in struct_table.by_name.items():
        type_lookup[struct_name] = struct_type

    # Add all enum types
    for enum_name, enum_type in enum_table.by_name.items():
        type_lookup[enum_name] = enum_type

    # Resolve variant associated types for each enum
    for enum_name in list(enum_table.by_name.keys()):
        enum_type = enum_table.by_name[enum_name]

        if not isinstance(enum_type, EnumType):
            continue  # Skip if not a regular EnumType

        # Resolve each variant's associated types
        needs_update = False
        resolved_variants = []
        for variant in enum_type.variants:
            resolved_assoc_types = []
            variant_needs_update = False
            for assoc_type in variant.associated_types:
                resolved_type = _resolve_type(assoc_type, type_lookup)
                resolved_assoc_types.append(resolved_type)
                if resolved_type is not assoc_type:
                    variant_needs_update = True
                    needs_update = True

            # Create new variant with resolved types if needed
            if variant_needs_update:
                from semantics.typesys import EnumVariantInfo
                resolved_variant = EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(resolved_assoc_types)
                )
                resolved_variants.append(resolved_variant)
            else:
                resolved_variants.append(variant)

        # Only update if we actually resolved something
        if needs_update:
            # Update enum variants in-place
            # Since EnumType is frozen (immutable), we need to use object.__setattr__
            object.__setattr__(enum_type, 'variants', tuple(resolved_variants))


def _resolve_type(ty: Type, type_lookup: Dict[str, Type]) -> Type:
    """Resolve a single type, recursively handling compound types.

    Args:
        ty: The type to resolve (may be UnknownType, ArrayType, DynamicArrayType, etc.)
        type_lookup: Dictionary mapping type names to concrete types

    Returns:
        Resolved type (UnknownType → StructType/EnumType/BuiltinType)
    """
    from semantics.typesys import ArrayType, DynamicArrayType
    from semantics.generics.types import GenericTypeRef

    # If it's an UnknownType, look it up in the type table
    if isinstance(ty, UnknownType):
        type_name = ty.name
        if type_name in type_lookup:
            return type_lookup[type_name]
        else:
            # Type not found - this will be caught during type validation (Pass 2)
            # For now, keep it as UnknownType
            return ty

    # If it's a GenericTypeRef (e.g., Maybe<i32>, Pair<i32, string>)
    # Look up the monomorphized concrete type
    elif isinstance(ty, GenericTypeRef):
        # GenericTypeRef's __str__ produces the full name like "Maybe<i32>" or "Pair<i32, string>"
        full_name = str(ty)
        if full_name in type_lookup:
            return type_lookup[full_name]
        else:
            # Monomorphized type not found - keep as GenericTypeRef
            # This will be caught during type validation if it's actually missing
            return ty

    # If it's an ArrayType, resolve the base type
    elif isinstance(ty, ArrayType):
        resolved_base = _resolve_type(ty.base_type, type_lookup)
        return ArrayType(base_type=resolved_base, size=ty.size)

    # If it's a DynamicArrayType, resolve the base type
    elif isinstance(ty, DynamicArrayType):
        resolved_base = _resolve_type(ty.base_type, type_lookup)
        return DynamicArrayType(base_type=resolved_base)

    # Already a concrete type (StructType, EnumType, BuiltinType, etc.)
    else:
        return ty
