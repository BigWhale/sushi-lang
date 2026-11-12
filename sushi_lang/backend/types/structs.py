"""The struct layout family: a struct value from its fields, and the auto-derived hash() and clone().

A struct and a tuple are an LLVM struct of their fields in the declaration order.
"""

from typing import Any, Sequence
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.semantics.generics.hashing import container_hash_kind
import llvmlite.ir as ir
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.semantics.derived_methods import register_hash_emitter_factory, register_clone_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend.types.value_hash import emit_value_hash, hash_override, reject_hash_arguments


def _emit_struct_hash(prim_type: Type) -> Any:
    """Create a hash() emitter function for struct types."""
    if not isinstance(prim_type, StructType):
        raise_internal_error("CE0032", type=type(prim_type).__name__)

    struct_type = prim_type

    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
               receiver_type: ir.Type) -> ir.Value:
        """Emit LLVM IR for struct.hash() method."""
        reject_hash_arguments(args)

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
    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
                receiver_type: ir.Type) -> ir.Value:
        from sushi_lang.backend.ownership import copy_out
        return copy_out(codegen, receiver_value, target_type)

    return emitter


register_clone_emitter_factory("struct", _emit_struct_clone)


def emit_struct_value(codegen: Any, struct_type: StructType,
                      values: Sequence[ir.Value]) -> ir.Value:
    """A value of `struct_type` from the value of each field, in the declaration order."""
    builder = require_builder(codegen)
    value: ir.Value = ir.Constant(codegen.types.ll_type(struct_type), ir.Undefined)
    for index, field_value in enumerate(values):
        value = builder.insert_value(value, field_value, index)
    return value


def emit_struct_into(codegen: Any, struct_type: StructType, parts: Sequence[ir.Value],
                     dest: ir.Value) -> None:
    """A value of `struct_type` built in the slot `dest`, one field at a time. The part of a
    field that moves by address is the address of its value; each other part is a value."""
    from sushi_lang.backend.gep_utils import gep_struct_field
    from sushi_lang.backend.types.arrays.runs import emit_aggregate_copy, moves_by_address

    builder = require_builder(codegen)
    for index, ((_name, field_type), part) in enumerate(
            zip(struct_type.fields, parts, strict=True)):
        field = gep_struct_field(codegen, dest, index, builder=builder)
        llvm_type = codegen.types.ll_type(field_type)
        if moves_by_address(llvm_type):
            emit_aggregate_copy(codegen, field, part, llvm_type)
        else:
            builder.store(part, field)
