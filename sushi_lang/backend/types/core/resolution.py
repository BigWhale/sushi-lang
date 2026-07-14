"""Shared type resolution helpers for mapping and sizing modules.

Consolidates duplicated type resolution logic to follow DRY principles.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import (
    UnknownType,
    StructType,
    EnumType,
    ResultType,
)

if TYPE_CHECKING:
    pass


def resolve_unknown_type(
    semantic_type: UnknownType,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType:
    """Resolve UnknownType to its actual struct or enum type.

    Args:
        semantic_type: The UnknownType to resolve.
        struct_table: Dictionary of struct types by name.
        enum_table: Dictionary of enum types by name.

    Returns:
        The resolved StructType or EnumType.

    Raises:
        InternalError: CE0020 if type cannot be resolved.
    """
    if semantic_type.name in struct_table:
        return struct_table[semantic_type.name]
    if semantic_type.name in enum_table:
        return enum_table[semantic_type.name]
    raise_internal_error("CE0020", type=semantic_type.name)


def resolve_generic_type_ref(
    semantic_type,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType | ResultType | None:
    """Resolve GenericTypeRef to its monomorphized type.

    Args:
        semantic_type: The GenericTypeRef to resolve.
        struct_table: Dictionary of struct types by name.
        enum_table: Dictionary of enum types by name.

    Returns:
        The resolved type, or None if not a GenericTypeRef.
        Returns ResultType for Result<T, E> patterns.

    Raises:
        InternalError: CE0045 if generic type cannot be resolved.
    """
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


def calculate_max_variant_size(
    enum_type: EnumType,
    size_calculator,
) -> int:
    """Calculate the maximum size needed for enum variant data.

    Args:
        enum_type: The enum type to analyze.
        size_calculator: Callable that takes a type and returns its size.

    Returns:
        Maximum size in bytes across all variants.
    """
    max_size = 0
    for variant in enum_type.variants:
        if variant.associated_types:
            variant_size = sum(size_calculator(t) for t in variant.associated_types)
            max_size = max(max_size, variant_size)
    return max_size
