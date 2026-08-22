"""The resolve pass: resolve every struct field and enum variant type to a concrete type."""

from typing import Dict
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType, Type, BuiltinType


def resolve_struct_field_types(
    struct_table: StructTable,
    enum_table: EnumTable
) -> None:
    """Resolve UnknownType references in struct fields to concrete types."""
    type_lookup: Dict[str, Type] = {}

    for builtin in BuiltinType:
        type_lookup[str(builtin).lower()] = builtin

    for struct_name, struct_type in struct_table.by_name.items():
        type_lookup[struct_name] = struct_type

    for enum_name, enum_type in enum_table.by_name.items():
        type_lookup[enum_name] = enum_type

    for struct_name in list(struct_table.by_name.keys()):
        struct_type = struct_table.by_name[struct_name]

        if not isinstance(struct_type, StructType):
            continue  # Skip if not a regular StructType

        resolved_fields = []
        needs_update = False
        for field_name, field_type in struct_type.fields:
            resolved_type = _resolve_type(field_type, type_lookup)
            resolved_fields.append((field_name, resolved_type))
            if resolved_type is not field_type:
                needs_update = True

        if needs_update:
            object.__setattr__(struct_type, 'fields', tuple(resolved_fields))


def resolve_enum_variant_types(
    struct_table: StructTable,
    enum_table: EnumTable
) -> None:
    """Resolve UnknownType references in enum variant associated types to concrete types."""
    type_lookup: Dict[str, Type] = {}

    for builtin in BuiltinType:
        type_lookup[str(builtin).lower()] = builtin

    for struct_name, struct_type in struct_table.by_name.items():
        type_lookup[struct_name] = struct_type

    for enum_name, enum_type in enum_table.by_name.items():
        type_lookup[enum_name] = enum_type

    for enum_name in list(enum_table.by_name.keys()):
        enum_type = enum_table.by_name[enum_name]

        if not isinstance(enum_type, EnumType):
            continue  # Skip if not a regular EnumType

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

            if variant_needs_update:
                from sushi_lang.semantics.typesys import EnumVariantInfo
                resolved_variant = EnumVariantInfo(
                    name=variant.name,
                    associated_types=tuple(resolved_assoc_types)
                )
                resolved_variants.append(resolved_variant)
            else:
                resolved_variants.append(variant)

        if needs_update:
            object.__setattr__(enum_type, 'variants', tuple(resolved_variants))


def _resolve_type(ty: Type, type_lookup: Dict[str, Type]) -> Type:
    """Resolve a single type, recursively handling compound types."""
    from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if isinstance(ty, UnknownType):
        type_name = ty.name
        if type_name in type_lookup:
            return type_lookup[type_name]
        else:
            return ty

    elif isinstance(ty, GenericTypeRef):
        full_name = str(ty)
        if full_name in type_lookup:
            return type_lookup[full_name]
        else:
            return ty

    elif isinstance(ty, ArrayType):
        resolved_base = _resolve_type(ty.base_type, type_lookup)
        return ArrayType(base_type=resolved_base, size=ty.size)

    elif isinstance(ty, DynamicArrayType):
        resolved_base = _resolve_type(ty.base_type, type_lookup)
        return DynamicArrayType(base_type=resolved_base)

    else:
        return ty
