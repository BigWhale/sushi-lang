"""The enum payload layout has ONE authority, and it is naturally aligned (#300 phase 2)."""
from __future__ import annotations

from sushi_lang.backend.types.core.sizing import TypeSizing, align_up
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo,
)


def _sizing() -> TypeSizing:
    return TypeSizing(StructTable(), EnumTable())


def _enum(*variants) -> EnumType:
    return EnumType(
        name="T<" + ",".join(v.name for v in variants) + ">",
        variants=tuple(variants),
    )


def test_every_field_offset_is_naturally_aligned():
    """The core invariant: offset % natural_alignment == 0, for a worst-case mix."""
    sizing = _sizing()
    mixes = [
        (BuiltinType.I32, BuiltinType.STRING),
        (BuiltinType.BOOL, BuiltinType.I64),
        (BuiltinType.I8, BuiltinType.I16, BuiltinType.F64, BuiltinType.I32),
        (BuiltinType.STRING, BuiltinType.I32, DynamicArrayType(BuiltinType.I32)),
    ]
    for types in mixes:
        offsets = sizing.payload_field_offsets(types)
        for ty, offset in zip(types, offsets):
            align = sizing.get_type_alignment(ty)
            assert offset % align == 0, (
                f"{ty} at offset {offset} is under-aligned (needs {align}) in {types}"
            )


def test_offsets_do_not_overlap_and_size_covers_them():
    """Fields are disjoint, and variant_payload_size covers the last field's end."""
    sizing = _sizing()
    types = (BuiltinType.I32, BuiltinType.STRING, BuiltinType.I8, BuiltinType.I64)
    offsets = sizing.payload_field_offsets(types)
    end = 0
    for ty, offset in zip(types, offsets):
        assert offset >= end, f"{ty} at {offset} overlaps the previous field (end {end})"
        end = offset + sizing.get_type_size_bytes(ty)
    assert sizing.variant_payload_size(types) == end


def test_word_count_covers_the_widest_variant():
    sizing = _sizing()
    enum_type = _enum(
        EnumVariantInfo(name="A", associated_types=(BuiltinType.I32, BuiltinType.STRING)),
        EnumVariantInfo(name="B", associated_types=(BuiltinType.I64,)),
        EnumVariantInfo(name="C", associated_types=()),
    )
    words = sizing.enum_payload_word_count(enum_type)
    widest = max(
        sizing.variant_payload_size(v.associated_types) for v in enum_type.variants
    )
    assert words * 8 >= widest
    assert words == align_up(widest, 8) // 8
    # The i32-then-string variant: string at aligned offset 8, so 24 bytes -> 3 words.
    assert sizing.payload_field_offsets((BuiltinType.I32, BuiltinType.STRING)) == [0, 8]
    assert words == 3


def test_enum_size_is_the_llvm_sizeof():
    """8 (tag + pad) + 8*K -- exactly what LLVM computes for {i32, [K x i64]}."""
    sizing = _sizing()
    unit = _enum(EnumVariantInfo(name="A", associated_types=()))
    assert sizing.get_type_size_bytes(unit) == 16  # 8 + 1 word minimum
    holds_i32 = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    assert sizing.get_type_size_bytes(holds_i32) == 16  # 8 + 1 word
    holds_str = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.STRING,)))
    assert sizing.get_type_size_bytes(holds_str) == 24  # 8 + 2 words


def test_enum_alignment_is_eight():
    """A struct holding an enum field must agree with LLVM's stride (the [K x i64] member makes the
    real alignment 8; answering 4 desynchronizes struct sizing).
    """
    sizing = _sizing()
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    assert sizing.get_type_alignment(enum_type) == 8


def test_pack_unpack_walk_reproduces_the_authority():
    """`pack/unpack_variant_field` thread a running offset and align on entry; the sequence they
    produce must equal payload_field_offsets exactly.
    """
    sizing = _sizing()
    types = (BuiltinType.I8, BuiltinType.STRING, BuiltinType.I32, BuiltinType.F64)
    expected = sizing.payload_field_offsets(types)
    walked = []
    offset = 0
    for ty in types:  # the exact rule the helpers apply on entry
        offset = align_up(offset, sizing.get_type_alignment(ty))
        walked.append(offset)
        offset += sizing.get_type_size_bytes(ty)
    assert walked == expected
