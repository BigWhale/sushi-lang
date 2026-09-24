"""Shared type resolution helpers for mapping and sizing modules."""
from __future__ import annotations

from sushi_lang.semantics.generics.interned import interned_name
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.typesys import (
    UnknownType,
    StructType,
    EnumType,
)


def require_named_type(
    semantic_type: UnknownType,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType:
    """The declaration `semantic_type` names, or the internal error CE0020.

    Named for its CONTRACT, not for its lookup. `semantics/type_resolution.py` has a
    `resolve_unknown_type` whose contract is the opposite -- a name it cannot place comes
    back UNCHANGED -- and the back end imports both, so one name for the two read alike at
    a call site and hid which one applied (#678). Here the caller is about to emit, a
    name with no declaration cannot be lowered, and the seam says so.
    """
    if semantic_type.name in struct_table:
        return struct_table[semantic_type.name]
    if semantic_type.name in enum_table:
        return enum_table[semantic_type.name]
    raise_internal_error("CE0020", type=semantic_type.name)


def require_generic_instance(
    semantic_type,
    struct_table: dict[str, StructType],
    enum_table: dict[str, EnumType],
) -> StructType | EnumType | None:
    """The monomorphized instance `semantic_type` names, or the internal error CE0045.

    Named for its CONTRACT, like `require_named_type` above. `TypeResolver` has a
    `resolve_generic_type_ref` whose contract is the opposite -- a reference it cannot
    place comes back UNCHANGED -- so one name for the two read alike at a call site and
    hid which one applied (#717). A type that is not a generic reference is not this
    seam's business, and answers None.
    """
    from sushi_lang.semantics.generics.types import GenericTypeRef

    if not isinstance(semantic_type, GenericTypeRef):
        return None

    # Result<T, E> resolves by concrete-name lookup like every other generic -- it is interned
    # into the enum table exactly like Maybe. It used to be special-cased into a ResultType here,
    # which is not an EnumType, so it matched none of the RAII predicates downstream (#179).
    concrete_name = interned_name(semantic_type.base_name, semantic_type.type_args)

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
