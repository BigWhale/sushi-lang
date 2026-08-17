"""LLVM emission for the auto-derived enum hash() method."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import EnumType, Type, ArrayType, DynamicArrayType, BuiltinType, StructType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory, register_clone_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend import enum_utils


def _emit_enum_hash(enum_type: Type) -> Any:
    """Create a hash() emitter function for enum types."""
    if not isinstance(enum_type, EnumType):
        raise_internal_error("CE0032", type=type(enum_type).__name__)

    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for enum.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
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
    builder = codegen.builder

    data_array = enum_utils.extract_enum_data(codegen, enum_value, name="enum_data")

    data_array_type = enum_value.type.elements[1]  # [N x i8]
    temp_alloca = builder.alloca(data_array_type, name="data_temp")
    builder.store(data_array, temp_alloca)

    data_ptr = builder.bitcast(temp_alloca, codegen.types.str_ptr, name="data_ptr")

    # Unpack and hash each associated value, at the offsets the ONE layout authority
    # gives (#300 phase 2). Natural alignment throughout: the payload base is 8-aligned
    # and the offsets are naturally aligned, so the `align=1` workaround (#145) is gone.
    hash_value = initial_hash
    field_offsets = codegen.types.payload_field_offsets(variant.associated_types)

    for assoc_idx, (assoc_type, field_offset) in enumerate(
            zip(variant.associated_types, field_offsets, strict=True)):
        llvm_type = codegen.types.ll_type(assoc_type)

        value_ptr_i8 = builder.gep(data_ptr, [ir.Constant(codegen.types.i32, field_offset)], name=f"assoc{assoc_idx}_ptr")
        value_ptr_typed = builder.bitcast(value_ptr_i8, ir.PointerType(llvm_type), name=f"assoc{assoc_idx}_ptr_typed")

        value = builder.load(value_ptr_typed, name=f"assoc{assoc_idx}_value")

        value_hash = _emit_associated_value_hash(codegen, value, assoc_type)

        hash_value = emit_fnv1a_combine(codegen, hash_value, value_hash)

    return hash_value


def _emit_associated_value_hash(codegen: Any, value: ir.Value, value_type: Type) -> ir.Value:
    """Emit code to get the hash of an associated value."""
    from sushi_lang.semantics.ast import MethodCall, Name

    require_builder(codegen)
    if isinstance(value_type, BuiltinType):
        import sushi_lang.backend.types.primitives.hashing  # noqa: F401
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    elif isinstance(value_type, StructType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    elif isinstance(value_type, EnumType):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    elif isinstance(value_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.sushi_stdlib.src.common import get_builtin_method

        hash_method = get_builtin_method(value_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(value_type))

        fake_call = MethodCall(
            receiver=Name(id="value", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, value, value.type, False
        )

    else:
        raise_internal_error("CE0052", type=str(value_type))


register_hash_emitter_factory("enum", _emit_enum_hash)


def _emit_enum_clone(target_type: Type) -> Any:
    """Create a clone() emitter for an enum type (#134)."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        from sushi_lang.backend.expressions.memory import emit_value_clone
        value = receiver_value
        if isinstance(value.type, ir.PointerType):
            value = codegen.builder.load(value, name="clone_recv")
        return emit_value_clone(codegen, value, target_type)

    return emitter


register_clone_emitter_factory("enum", _emit_enum_clone)
