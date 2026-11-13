"""LLVM emission for the auto-derived array hash() method."""

from typing import Any, Sequence
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, Type
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i32_const
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend import gep_utils
from sushi_lang.backend.utils import require_builder
from sushi_lang.semantics.derived_methods import register_hash_emitter_factory
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine
from sushi_lang.backend.types.value_hash import emit_value_hash, reject_hash_arguments
from sushi_lang.backend.memory.allocas import entry_alloca


def _emit_fixed_array_hash(array_type: ArrayType) -> Any:
    """Create a hash() emitter function for fixed array types."""
    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
               receiver_type: ir.Type) -> ir.Value:
        """Emit LLVM IR for fixed_array.hash() method."""
        reject_hash_arguments(args)

        builder = require_builder(codegen)
        u64 = ir.IntType(INT64_BIT_WIDTH)

        hash_value = emit_fnv1a_init(codegen)

        # Two callers, two shapes. The method dispatcher hands an address down from
        # `as_fixed_array_address` (#480). The DERIVED struct hash
        # (`backend/types/value_hash.py::emit_value_hash`) hands a field value, because it
        # walks a loaded struct and no field of it has an address. A hash only READS, so
        # spilling that value is sound -- the read/write split the seam draws.
        if isinstance(receiver_value.type, ir.PointerType):
            array_ptr = receiver_value
        else:
            array_ptr = entry_alloca(builder, receiver_type, name="array_temp")
            builder.store(receiver_value, array_ptr)

        for i in range(array_type.size):
            zero = ZERO_I32
            index = make_i32_const(i)
            element_ptr = builder.gep(array_ptr, [zero, index], name=f"elem_{i}_ptr")

            element_value = builder.load(element_ptr, name=f"elem_{i}")

            element_hash = emit_value_hash(codegen, element_value, array_type.base_type)

            hash_value = emit_fnv1a_combine(codegen, hash_value, element_hash)

        length_u64 = ir.Constant(u64, array_type.size)
        hash_value = emit_fnv1a_combine(codegen, hash_value, length_u64)

        return hash_value

    return emitter


def _emit_dynamic_array_hash(array_type: DynamicArrayType) -> Any:
    """Create a hash() emitter function for dynamic array types."""
    def emitter(codegen: Any, args: Sequence[ir.Value], receiver_value: ir.Value,
               receiver_type: ir.Type) -> ir.Value:
        """Emit LLVM IR for dynamic_array.hash() method."""
        reject_hash_arguments(args)

        builder = require_builder(codegen)
        i32 = ir.IntType(INT32_BIT_WIDTH)
        u64 = ir.IntType(INT64_BIT_WIDTH)

        hash_value_alloca = entry_alloca(builder, u64, name="hash_value")
        initial_hash = emit_fnv1a_init(codegen)
        builder.store(initial_hash, hash_value_alloca)

        if isinstance(receiver_value.type, ir.PointerType):
            array_struct_ptr = receiver_value
        else:
            array_struct_ptr = entry_alloca(builder, receiver_type, name="array_struct_temp")
            builder.store(receiver_value, array_struct_ptr)

        current_len = gep_utils.load_len(codegen, array_struct_ptr, "array_len", builder=builder)

        data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, array_struct_ptr)
        data_ptr = builder.load(data_ptr_ptr, name="array_data")

        counter = entry_alloca(builder, i32, name="counter")
        zero_i32 = ZERO_I32
        builder.store(zero_i32, counter)

        loop_header = builder.append_basic_block(name="hash_loop_header")
        loop_body = builder.append_basic_block(name="hash_loop_body")
        loop_exit = builder.append_basic_block(name="hash_loop_exit")

        builder.branch(loop_header)

        builder.position_at_end(loop_header)
        current_counter = builder.load(counter)
        cond = builder.icmp_unsigned('<', current_counter, current_len)
        builder.cbranch(cond, loop_body, loop_exit)

        builder.position_at_end(loop_body)

        element_ptr = builder.gep(data_ptr, [current_counter], name="element_ptr")
        element_value = builder.load(element_ptr, name="element")

        element_hash = emit_value_hash(codegen, element_value, array_type.base_type)

        current_hash = builder.load(hash_value_alloca)
        new_hash = emit_fnv1a_combine(codegen, current_hash, element_hash)
        builder.store(new_hash, hash_value_alloca)

        one_i32 = make_i32_const(1)
        next_counter = builder.add(current_counter, one_i32)
        builder.store(next_counter, counter)

        builder.branch(loop_header)

        builder.position_at_end(loop_exit)
        final_hash = builder.load(hash_value_alloca)

        length_u64 = builder.zext(current_len, u64)
        final_hash = emit_fnv1a_combine(codegen, final_hash, length_u64)

        return final_hash

    return emitter


def _make_array_hash_emitter(array_type: Type) -> Any:
    """Build the hash() emitter for an array type, fixed or dynamic."""
    if isinstance(array_type, ArrayType):
        return _emit_fixed_array_hash(array_type)
    if isinstance(array_type, DynamicArrayType):
        return _emit_dynamic_array_hash(array_type)
    raise_internal_error("CE0041", type=type(array_type).__name__)


register_hash_emitter_factory("array", _make_array_hash_emitter)
