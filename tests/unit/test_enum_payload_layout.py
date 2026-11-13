"""The enum payload layout has ONE authority, and it is naturally aligned (#300 phase 2)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from llvmlite import ir

from sushi_lang.backend.enum_utils import unpack_all_variant_fields
from sushi_lang.backend.constants.enum_initializers import constant_of, enum_initializer
from sushi_lang.backend.expressions.enums import emit_enum_variant_value
from sushi_lang.backend.types.core import LLVMTypeSystem
from sushi_lang.backend.types.core.sizing import TypeSizing, align_up
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.const_eval import AggregateConstant, ScalarConstant
from sushi_lang.semantics.passes.collect import StructTable, EnumTable
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo, StructType,
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
        for ty, offset in zip(types, offsets, strict=True):
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
    for ty, offset in zip(types, offsets, strict=True):
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
    # The i32-then-string variant: the 24-byte string at aligned offset 8, so 32 bytes -> 4 words.
    assert sizing.payload_field_offsets((BuiltinType.I32, BuiltinType.STRING)) == [0, 8]
    assert words == 4


def test_enum_size_is_the_llvm_sizeof():
    """8 (tag + pad) + 8*K -- exactly what LLVM computes for {i32, [K x i64]}."""
    sizing = _sizing()
    unit = _enum(EnumVariantInfo(name="A", associated_types=()))
    assert sizing.get_type_size_bytes(unit) == 16  # 8 + 1 word minimum
    holds_i32 = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    assert sizing.get_type_size_bytes(holds_i32) == 16  # 8 + 1 word
    holds_str = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.STRING,)))
    assert sizing.get_type_size_bytes(holds_str) == 32  # 8 + 3 words


def test_enum_alignment_is_eight():
    """A struct holding an enum field must agree with LLVM's stride (the [K x i64] member makes the
    real alignment 8; answering 4 desynchronizes struct sizing).
    """
    sizing = _sizing()
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    assert sizing.get_type_alignment(enum_type) == 8


def _unpacked_offsets(types: LLVMTypeSystem, field_types) -> list[int]:
    """Run the real `unpack_all_variant_fields` and read back the byte offset of each load."""
    module = ir.Module()
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), [ir.PointerType(types.i8)]), "f")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    codegen = SimpleNamespace(types=types, builder=builder)
    data_ptr = fn.args[0]
    values = unpack_all_variant_fields(codegen, data_ptr, list(field_types))
    offsets = []
    for value in values:
        address = value.operands[0]
        while address is not data_ptr and getattr(address, "opname", "") == "bitcast":
            address = address.operands[0]
        offsets.append(0 if address is data_ptr else address.indices[0].constant)
    return offsets


def test_unpack_reads_the_authority():
    """`unpack_all_variant_fields` loads each field at the offset payload_field_offsets gives."""
    types = LLVMTypeSystem()
    field_types = (BuiltinType.I8, BuiltinType.STRING, BuiltinType.I32, BuiltinType.F64)
    assert _unpacked_offsets(types, field_types) == types.payload_field_offsets(field_types)


def test_unpack_has_no_layout_of_its_own():
    """A changed authority moves every load: the helper keeps no second offset walk."""
    types = LLVMTypeSystem()
    field_types = (BuiltinType.I32, BuiltinType.I32, BuiltinType.I64)
    types.sizing.payload_field_offsets = lambda _ts: [8, 16, 24]
    assert _unpacked_offsets(types, field_types) == [8, 16, 24]


def _packed_offsets(types: LLVMTypeSystem, field_types) -> list[int]:
    """Run the real enum packer and read back the byte offset of each store."""
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=tuple(field_types)))
    module = ir.Module()
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), []), "f")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    codegen = SimpleNamespace(types=types, builder=builder)
    values = [ir.Constant(types.ll_type(ty), None) for ty in field_types]
    emit_enum_variant_value(codegen, enum_type, enum_type.variants[0], values)
    offsets = []
    for instruction in fn.blocks[0].instructions:
        if instruction.opname != "store" or instruction.operands[0] not in values:
            continue
        address = instruction.operands[1]
        while getattr(address, "opname", "") == "bitcast":
            address = address.operands[0]
        offsets.append(address.indices[0].constant if address.opname == "getelementptr" else 0)
    return offsets


def test_pack_reads_the_authority():
    """The enum packer stores each payload value at the offset payload_field_offsets gives."""
    types = LLVMTypeSystem()
    field_types = (BuiltinType.I8, BuiltinType.STRING, BuiltinType.I32, BuiltinType.F64)
    assert _packed_offsets(types, field_types) == types.payload_field_offsets(field_types)


def test_pack_has_no_layout_of_its_own():
    """A changed authority moves every store: the packer keeps no second offset walk."""
    types = LLVMTypeSystem()
    field_types = (BuiltinType.I32, BuiltinType.I32, BuiltinType.I64)
    types.sizing.payload_field_offsets = lambda _ts: [8, 16, 24]
    assert _packed_offsets(types, field_types) == [8, 16, 24]


def test_constant_has_no_layout_of_its_own():
    """The one converter of an enum constant writes each payload value at the offset of
    the authority: a changed authority moves the bytes."""
    types = LLVMTypeSystem()
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,
                                                                   BuiltinType.I32)))
    constant = k.Aggregate(enum_type, (k.Int(5, BuiltinType.I32), k.Int(7, BuiltinType.I32)),
                           "A")
    natural = enum_initializer(constant, types, None, "")
    assert [word.constant for word in natural.constant[1].constant] == [5 | 7 << 32]
    types.sizing.payload_field_offsets = lambda _ts: [4, 0]
    moved = enum_initializer(constant, types, None, "")
    assert [word.constant for word in moved.constant[1].constant] == [7 | 5 << 32]


def _one(value: int) -> ScalarConstant:
    return ScalarConstant(value, BuiltinType.I32)


POINT = StructType(name="Point", fields=(("x", BuiltinType.I32), ("y", BuiltinType.I32)))


def test_a_global_with_a_struct_payload_writes_each_field_at_the_authority_offset() -> None:
    """A struct value in a payload is a SLIR constant (A5). The one converter writes its
    fields at the offsets of the layout authority, inside the offset of the payload."""
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(POINT,)))
    value = AggregateConstant([AggregateConstant([_one(1), _one(2)], POINT)], enum_type, "A")
    constant = constant_of(value, None, None)
    assert constant == k.Aggregate(enum_type, (k.Aggregate(
        POINT, (k.Int(1, BuiltinType.I32), k.Int(2, BuiltinType.I32))),), "A")
    words = enum_initializer(constant, LLVMTypeSystem(), None, "").constant[1].constant
    assert [word.constant for word in words] == [1 | 2 << 32]


@pytest.mark.parametrize("payload, words", [
    (AggregateConstant([_one(1), _one(2)], ArrayType(BuiltinType.I32, 2)), [1 | 2 << 32]),
    (AggregateConstant([AggregateConstant([_one(3)], ArrayType(BuiltinType.I32, 1))],
                       StructType(name="Row", fields=(("row", ArrayType(BuiltinType.I32, 1)),))),
     [3]),
], ids=["array", "array in a struct"])
def test_a_global_with_an_array_payload_writes_each_element_at_its_stride(
        payload: AggregateConstant, words: list[int]) -> None:
    """An array value in a payload, also in a struct, is a SLIR constant (A6). The one
    converter writes each element at its stride, inside the offset of the payload."""
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(payload.semantic_type,)))
    constant = constant_of(AggregateConstant([payload], enum_type, "A"), None, None)
    written = enum_initializer(constant, LLVMTypeSystem(), None, "").constant[1].constant
    assert [word.constant for word in written] == words


def test_a_global_of_an_enum_value_is_its_slir_constant() -> None:
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    value = AggregateConstant([_one(5)], enum_type, "A")
    assert constant_of(value, None, None) == k.Aggregate(
        enum_type, (k.Int(5, BuiltinType.I32),), "A")


def _faulty(constant: k.Aggregate) -> None:
    """The one converter refuses `constant` with an internal error; it never answers None."""
    with pytest.raises(InternalCompilerError):
        enum_initializer(constant, LLVMTypeSystem(), None, "")


def test_the_converter_checks_the_type_of_each_part() -> None:
    """The one check of a variant runs on every path of a constant: a part of another
    integer type than its payload field is an internal error."""
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.I32,)))
    _faulty(k.Aggregate(enum_type, (k.Int(5, BuiltinType.I64),), "A"))


def test_the_converter_checks_a_nested_variant() -> None:
    inner = _enum(EnumVariantInfo(name="X", associated_types=(BuiltinType.I32,)))
    outer = EnumType(name="Outer", variants=(EnumVariantInfo(name="A", associated_types=(inner,)),))
    _faulty(k.Aggregate(outer, (k.Aggregate(inner, (k.Int(5, BuiltinType.U8),), "X"),), "A"))


def test_the_converter_refuses_a_variant_that_its_enum_does_not_declare() -> None:
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=()))
    _faulty(k.Aggregate(enum_type, (), "B"))


def test_a_string_part_with_no_finisher_is_an_internal_error() -> None:
    enum_type = _enum(EnumVariantInfo(name="A", associated_types=(BuiltinType.STRING,)))
    _faulty(k.Aggregate(enum_type, (k.text("x"),), "A"))
