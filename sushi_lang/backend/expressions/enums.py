"""Enum constructor operations for the Sushi language compiler."""
from __future__ import annotations
from typing import TYPE_CHECKING, Sequence

from llvmlite import ir
from sushi_lang.backend import enum_utils
from sushi_lang.backend.memory.allocas import entry_alloca

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import EnumType, EnumVariantInfo


def emit_enum_variant_value(
    codegen: 'LLVMCodegen',
    enum_type: 'EnumType',
    variant: 'EnumVariantInfo',
    values: Sequence[ir.Value]
) -> ir.Value:
    """An enum value of the variant `variant` of `enum_type`, with each payload value
    packed at its offset.

    The caller checked the variant and the payload values against it. The payload values
    are evaluated already, and the enum takes each one as it is.
    """
    variant_index = enum_type.get_variant_index(variant.name)
    llvm_enum_type = codegen.types.get_enum_type(enum_type)

    enum_value = enum_utils.construct_enum_variant(
        codegen, llvm_enum_type, variant_index,
        data=None, name_prefix=f"{enum_type.name}_{variant.name}"
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

    # Each value goes at its offset from the ONE layout authority, through the address
    # helper that the payload reads use too, so construct and extract cannot disagree.
    field_types = variant.associated_types
    field_offsets = codegen.types.payload_field_offsets(field_types)
    for i, (value, field_type, offset) in enumerate(
            zip(values, field_types, field_offsets, strict=True)):
        address = enum_utils.variant_field_address(codegen, data_ptr, field_type, offset,
                                                   name=f"arg{i}")
        codegen.builder.store(value, address)

    packed_data = codegen.builder.load(temp_alloca, name="packed_data")
    return enum_utils.set_enum_data(
        codegen, enum_value, packed_data,
        name=f"{enum_type.name}_{variant.name}_data"
    )
