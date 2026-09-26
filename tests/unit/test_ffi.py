"""Unit tests for FFI internals that the .sushi corpus cannot reach directly."""
from __future__ import annotations


































def test_foreign_ptr_sizing():
    """ForeignPtrType must size and align like the other pointer types (issue #85)."""
    from sushi_lang.backend.types.core.sizing import TypeSizing
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable
    from sushi_lang.semantics.typesys import ForeignPtrType

    sizing = TypeSizing(StructTable(), EnumTable())
    assert sizing.get_type_size_bytes(ForeignPtrType()) == 8
    assert sizing.get_type_alignment(ForeignPtrType()) == 8


def test_foreign_ptr_in_enum_payload_sizes():
    """An enum variant carrying `ptr` (e.g. Result<ptr, E>'s Ok) sizes without CE0021."""
    from sushi_lang.backend.types.core.sizing import TypeSizing
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable
    from sushi_lang.semantics.typesys import (
        ForeignPtrType, EnumType, EnumVariantInfo, BuiltinType,
    )

    result_like = EnumType(
        name="Result<ptr, StdError>",
        variants=(
            EnumVariantInfo(name="Ok", associated_types=(ForeignPtrType(),)),
            EnumVariantInfo(name="Err", associated_types=(BuiltinType.I32,)),
        ),
    )
    sizing = TypeSizing(StructTable(), EnumTable())
    # Tag+pad (8) + one i64 payload word (the 8-byte ptr) = 16 (#300 phase 2 layout).
    assert sizing.get_type_size_bytes(result_like) == 16


def test_contains_foreign_ptr_walk():
    """The shared CE5008/CE5002 walk sees ptr through Result/Maybe-style wrappers."""
    from sushi_lang.semantics.type_predicates import contains_foreign_ptr
    from sushi_lang.semantics.typesys import (
        ForeignPtrType, BuiltinType, EnumType, EnumVariantInfo, StructType,
    )
    from sushi_lang.semantics.generics.types import GenericTypeRef

    assert contains_foreign_ptr(ForeignPtrType())
    # A Result<ptr, i32> is an ordinary interned enum; the walk reaches ptr through its variants.
    result_ptr = EnumType(
        name="Result<ptr, i32>",
        variants=(
            EnumVariantInfo(name="Ok", associated_types=(ForeignPtrType(),)),
            EnumVariantInfo(name="Err", associated_types=(BuiltinType.I32,)),
        ),
    )
    assert contains_foreign_ptr(result_ptr)
    # the typecheck pass-era representation: an unresolved Result<ptr, E> annotation.
    assert contains_foreign_ptr(
        GenericTypeRef(base_name="Result", type_args=(ForeignPtrType(), BuiltinType.I32))
    )
    assert not contains_foreign_ptr(BuiltinType.I64)
    # Struct fields may carry ptr; the predicate still reports the exposure when
    # the concrete StructType is handed to it (the FENCE only checks fn signatures).
    handle = StructType(name="Handle", fields=(("raw", ForeignPtrType()),))
    assert contains_foreign_ptr(handle)
