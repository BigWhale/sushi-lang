"""Enum constructor operations for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Sequence

from llvmlite import ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import enum_utils
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo


def emit_enum_variant_value(
    codegen: 'LLVMCodegen',
    enum_type: 'EnumType',
    variant_name: str,
    values: Sequence[ir.Value]
) -> ir.Value:
    """An enum value of one variant, with each payload value packed at its offset.

    The payload values are evaluated already, and the enum takes each one as it is.
    """
    variant = _variant(enum_type, variant_name, len(values))
    variant_index = enum_type.get_variant_index(variant_name)
    llvm_enum_type = codegen.types.get_enum_type(enum_type)

    enum_value = enum_utils.construct_enum_variant(
        codegen, llvm_enum_type, variant_index,
        data=None, name_prefix=f"{enum_type.name}_{variant_name}"
    )
    if not values:
        return enum_value

    # Allocate temporary storage for the data. The [K x i64] member type makes the
    # alloca 8-aligned (#300 phase 2), so the naturally aligned field offsets below
    # are naturally aligned absolutely.
    data_array_type = llvm_enum_type.elements[1]  # [K x i64] array
    # ENTRY block, not the current position: constructing an enum inside a loop
    # would otherwise allocate again every iteration and never release it until
    # the function returned (BUGS.md B1). One slot per site is enough, because the
    # packed payload is loaded back out before the expression finishes.
    temp_alloca = entry_alloca(codegen.builder, data_array_type, "enum_data_temp")
    data_ptr = codegen.builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Pack each value at its offset from the ONE layout authority -- extraction,
    # destroy, clone and hash all read the same offsets, so construct and extract
    # cannot disagree (they used to derive offsets from two different size walks).
    # Natural alignment throughout: the base is 8-aligned and the offset is naturally
    # aligned (#300 phase 2), so the packed-layout `align=1` workaround (#145) is gone.
    field_offsets = codegen.types.payload_field_offsets(variant.associated_types)
    for i, (value, offset) in enumerate(zip(values, field_offsets, strict=True)):
        arg_ptr_i8 = codegen.builder.gep(data_ptr, [ir.Constant(codegen.types.i32, offset)],
                                         name=f"arg{i}_ptr")
        arg_ptr_typed = codegen.builder.bitcast(arg_ptr_i8, ir.PointerType(value.type),
                                                name=f"arg{i}_ptr_typed")
        codegen.builder.store(value, arg_ptr_typed)

    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    return enum_utils.set_enum_data(
        codegen, enum_value, packed_data,
        name=f"{enum_type.name}_{variant_name}_data"
    )


def _variant(enum_type: 'EnumType', variant_name: str, count: int) -> 'EnumVariantInfo':
    """The variant `variant_name` of `enum_type`, which takes `count` payload values."""
    variant = enum_type.get_variant(variant_name)
    if variant is None:
        raise_internal_error("CE0034", variant=variant_name, enum=enum_type.name)
    if count != len(variant.associated_types):
        raise_internal_error("CE0096", operation=f"Variant {enum_type.name}.{variant_name} expects {len(variant.associated_types)} arguments, got {count}"
        )
    return variant
