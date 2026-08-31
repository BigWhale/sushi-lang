"""The resolve pass: every struct field, enum variant and constant type made concrete."""

from typing import Dict, Optional
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.typesys import StructType, EnumType, UnknownType, Type, BuiltinType


def _type_lookup(struct_table: StructTable, enum_table: EnumTable) -> Dict[str, Type]:
    """Every name a written type may spell, mapped to the table entry it means."""
    lookup: Dict[str, Type] = {str(builtin).lower(): builtin for builtin in BuiltinType}
    lookup.update(struct_table.by_name)
    lookup.update(enum_table.by_name)
    return lookup


def resolve_struct_field_types(
    struct_table: StructTable,
    enum_table: EnumTable
) -> None:
    """Resolve UnknownType references in struct fields to concrete types."""
    type_lookup = _type_lookup(struct_table, enum_table)

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
    type_lookup = _type_lookup(struct_table, enum_table)

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


def resolve_constant_types(constants, const_defs, struct_table: StructTable,
                           enum_table: EnumTable) -> None:
    """Resolve a constant's DECLARED type, so every reader sees the table entry.

    A constant's type is written by the AST builder before any table exists, so
    `const Handle OUT = ...` collects as `UnknownType("Handle")`. That was invisible
    while every constant was a scalar; a constant of struct type made it visible at
    once, because an extension method resolves on the receiver's type and an
    `UnknownType` matches nothing.

    Resolving HERE and not at the read sites is what keeps it one seam: the signature
    and the declaration are both updated, so the typecheck pass, the borrow pass and the
    backend all read a resolved type without any of them remembering to ask.
    """
    type_lookup = _type_lookup(struct_table, enum_table)

    def resolved(ty: Optional[Type]) -> Optional[Type]:
        return None if ty is None else _resolve_type(ty, type_lookup)

    if constants is not None:
        for sig in constants.by_name.values():
            sig.const_type = resolved(sig.const_type)
        for unit_sigs in constants.by_unit.values():
            for sig in unit_sigs.values():
                sig.const_type = resolved(sig.const_type)

    for const_def in const_defs:
        const_def.ty = resolved(const_def.ty)


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
