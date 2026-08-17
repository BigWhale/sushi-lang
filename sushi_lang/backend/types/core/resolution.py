"""Shared type resolution helpers for mapping and sizing modules."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import (
    UnknownType,
    StructType,
    EnumType,
)

if TYPE_CHECKING:
    pass


def resolve_unknown_type(
    semantic_type: UnknownType,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType:
    """Resolve UnknownType to its actual struct or enum type."""
    if semantic_type.name in struct_table:
        return struct_table[semantic_type.name]
    if semantic_type.name in enum_table:
        return enum_table[semantic_type.name]
    raise_internal_error("CE0020", type=semantic_type.name)


def resolve_generic_type_ref(
    semantic_type,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType | None:
    """Resolve GenericTypeRef to its monomorphized type."""
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if not isinstance(semantic_type, GenericTypeRef):
        return None

    # Result<T, E> resolves by concrete-name lookup like every other generic -- it is interned
    # into the enum table exactly like Maybe. It used to be special-cased into a ResultType here,
    # which is not an EnumType, so it matched none of the RAII predicates downstream (#179).
    type_args_str = ", ".join(str(arg) for arg in semantic_type.type_args)
    concrete_name = f"{semantic_type.base_name}<{type_args_str}>"

    if concrete_name in enum_table:
        return enum_table[concrete_name]

    if concrete_name in struct_table:
        return struct_table[concrete_name]

    raise_internal_error("CE0045", type=concrete_name)


# calculate_max_variant_size was RETIRED with the aligned enum payload layout (#300
# phase 2): a plain sum of field sizes under-sizes an aligned layout, and two
# derivations of one layout is how construct and extract could disagree. The one
# authority is TypeSizing.payload_field_offsets / variant_payload_size /
# enum_payload_word_count (backend/types/core/sizing.py).
