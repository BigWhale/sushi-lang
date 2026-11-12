"""LLVM emission for the auto-derived enum hash() method."""

from typing import Any, Sequence
from sushi_lang.semantics.typesys import EnumType, Type
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.semantics.derived_methods import register_hash_emitter_factory, register_clone_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend.types.value_hash import emit_value_hash, reject_hash_arguments
from sushi_lang.backend import enum_utils
from sushi_lang.backend.memory.allocas import entry_alloca


def _emit_enum_hash(enum_type: Type) -> Any:
    """Create a hash() emitter function for enum types."""
    if not isinstance(enum_type, EnumType):
        raise_internal_error("CE0032", type=type(enum_type).__name__)

    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
               receiver_type: ir.Type) -> ir.Value:
        """Emit LLVM IR for enum.hash() method."""
        reject_hash_arguments(args)

        builder = require_builder(codegen)
        u64 = ir.IntType(INT64_BIT_WIDTH)
        i32 = codegen.types.i32

        if isinstance(receiver_value.type, ir.PointerType):
            enum_value = builder.load(receiver_value, name="enum_val")
        else:
            enum_value = receiver_value

        tag = enum_utils.extract_enum_tag(codegen, enum_value, name="enum_tag")
        tag_u64 = builder.zext(tag, u64)

        hash_value = emit_fnv1a_init(codegen)
        hash_value = emit_fnv1a_combine(codegen, hash_value, tag_u64)

        # If all variants have no associated data, just return tag-based hash
        has_any_data = any(len(v.associated_types) > 0 for v in enum_type.variants)
        if not has_any_data:
            return hash_value

        merge_block = builder.append_basic_block(name="hash_merge")
        hash_phi_incoming = []

        switch_block = builder.block

        switch = builder.switch(tag, merge_block)

        for variant_idx, variant in enumerate(enum_type.variants):
            if len(variant.associated_types) == 0:
                continue

            variant_block = builder.append_basic_block(name=f"hash_variant_{variant.name}")
            switch.add_case(ir.Constant(i32, variant_idx), variant_block)
            builder.position_at_end(variant_block)

            variant_hash = _emit_variant_data_hash(codegen, enum_value, variant, hash_value)

            current_block = builder.block
            hash_phi_incoming.append((variant_hash, current_block))
            builder.branch(merge_block)

        builder.position_at_end(merge_block)

        if not hash_phi_incoming:
            return hash_value

        hash_phi = builder.phi(u64, name="final_hash")

        for hash_val, block in hash_phi_incoming:
            hash_phi.add_incoming(hash_val, block)

        hash_phi.add_incoming(hash_value, switch_block)

        return hash_phi

    return emitter


def _emit_variant_data_hash(codegen: Any, enum_value: ir.Value, variant: Any, initial_hash: ir.Value) -> ir.Value:
    """Emit code to hash the associated data for a specific enum variant."""
    builder = require_builder(codegen)

    data_array = enum_utils.extract_enum_data(codegen, enum_value, name="enum_data")
    temp_alloca = entry_alloca(builder, data_array.type, name="data_temp")
    builder.store(data_array, temp_alloca)
    data_ptr = builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Each payload value is read at the offset of the ONE layout authority, through the
    # helper that every payload read uses.
    values = enum_utils.unpack_all_variant_fields(
        codegen, data_ptr, list(variant.associated_types), name_prefix="assoc")
    hash_value = initial_hash
    for value, assoc_type in zip(values, variant.associated_types, strict=True):
        hash_value = emit_fnv1a_combine(codegen, hash_value, emit_value_hash(codegen, value, assoc_type))
    return hash_value


register_hash_emitter_factory("enum", _emit_enum_hash)


def _emit_enum_clone(target_type: Type) -> Any:
    """Create a clone() emitter for an enum type (#134)."""
    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
                receiver_type: ir.Type) -> ir.Value:
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, receiver_value, target_type)

    return emitter


register_clone_emitter_factory("enum", _emit_enum_clone)
