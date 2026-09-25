"""LLVM emission for the auto-derived struct hash() method."""

from typing import Any, Optional
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.generics.hashing import container_hash_kind
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory, register_clone_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend.types.value_hash import emit_value_hash, hash_override, reject_hash_arguments


def _emit_struct_hash(prim_type: Type) -> Any:
    """Create a hash() emitter function for struct types."""
    if not isinstance(prim_type, StructType):
        raise_internal_error("CE0032", type=type(prim_type).__name__)

    struct_type = prim_type

    def emitter(codegen: Any, call: Optional[MethodCall], receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for struct.hash() method."""
        reject_hash_arguments(call)

        builder = require_builder(codegen)

        hash_value = emit_fnv1a_init(codegen)

        for field_idx, (field_name, field_type) in enumerate(struct_type.fields):
            if isinstance(receiver_value.type, ir.PointerType):
                struct_value = builder.load(receiver_value, name="struct_val")
            else:
                struct_value = receiver_value

            field_value = builder.extract_value(struct_value, field_idx, name=f"field_{field_name}")

            field_hash = _emit_field_hash(codegen, field_value, field_type)

            hash_value = emit_fnv1a_combine(codegen, hash_value, field_hash)

        return hash_value

    return emitter


def _emit_field_hash(codegen: Any, field_value: ir.Value, field_type: Type) -> ir.Value:
    """Emit code to get the hash of a field value."""
    builder = require_builder(codegen)

    # A plain nested struct is walked field by field rather than through its own
    # registered hash, so the combine order of the outer struct is one flat sequence.
    # A CONTAINER is not flattened: its fields are its implementation (`List@(T).data`
    # is a raw pointer), and it answers through its own hash, which reads what it holds
    # (#628). A struct with a `Hashable` implementation is not flattened either: the
    # override is terminal in every position (#871).
    if (isinstance(field_type, StructType)
            and container_hash_kind(field_type) is None
            and hash_override(codegen, field_type) is None):
        nested_hash = emit_fnv1a_init(codegen)

        for nested_idx, (nested_name, nested_type) in enumerate(field_type.fields):
            nested_field = builder.extract_value(field_value, nested_idx, name=f"nested_{nested_name}")

            nested_field_hash = _emit_field_hash(codegen, nested_field, nested_type)

            nested_hash = emit_fnv1a_combine(codegen, nested_hash, nested_field_hash)

        return nested_hash

    return emit_value_hash(codegen, field_value, field_type)


register_hash_emitter_factory("struct", _emit_struct_hash)


def _emit_struct_clone(target_type: Type) -> Any:
    """Create a clone() emitter for a struct type (#134)."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
                receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, receiver_value, target_type)

    return emitter


register_clone_emitter_factory("struct", _emit_struct_clone)
