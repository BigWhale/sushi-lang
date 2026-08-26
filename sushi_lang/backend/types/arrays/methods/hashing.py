"""LLVM emission for the auto-derived array hash() method."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType, Type, BuiltinType, StructType, EnumType
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i32_const
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_hash_emitter_factory, get_builtin_method
from sushi_lang.backend.types.hash_utils import emit_fnv1a_init, emit_fnv1a_combine


def _emit_fixed_array_hash(array_type: ArrayType) -> Any:
    """Create a hash() emitter function for fixed array types."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for fixed_array.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)

        hash_value = emit_fnv1a_init(codegen)

        # Two callers, two shapes. The method dispatcher hands an address down from
        # `as_fixed_array_address` (#480). The DERIVED struct hash
        # (`backend/types/structs.py::_emit_field_hash`) hands a field value, because it
        # walks a loaded struct and no field of it has an address. A hash only READS, so
        # spilling that value is sound -- the read/write split the seam draws.
        if isinstance(receiver_value.type, ir.PointerType):
            array_ptr = receiver_value
        else:
            array_ptr = builder.alloca(receiver_type, name="array_temp")
            builder.store(receiver_value, array_ptr)

        for i in range(array_type.size):
            zero = ZERO_I32
            index = make_i32_const(i)
            element_ptr = builder.gep(array_ptr, [zero, index], name=f"elem_{i}_ptr")

            element_value = builder.load(element_ptr, name=f"elem_{i}")

            element_hash = _emit_element_hash(codegen, element_value, array_type.base_type)

            hash_value = emit_fnv1a_combine(codegen, hash_value, element_hash)

        length_u64 = ir.Constant(u64, array_type.size)
        hash_value = emit_fnv1a_combine(codegen, hash_value, length_u64)

        return hash_value

    return emitter


def _emit_dynamic_array_hash(array_type: DynamicArrayType) -> Any:
    """Create a hash() emitter function for dynamic array types."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Emit LLVM IR for dynamic_array.hash() method."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        i32 = ir.IntType(INT32_BIT_WIDTH)
        u64 = ir.IntType(INT64_BIT_WIDTH)

        hash_value_alloca = builder.alloca(u64, name="hash_value")
        initial_hash = emit_fnv1a_init(codegen)
        builder.store(initial_hash, hash_value_alloca)

        if isinstance(receiver_value.type, ir.PointerType):
            array_struct_ptr = receiver_value
        else:
            array_struct_ptr = builder.alloca(receiver_type, name="array_struct_temp")
            builder.store(receiver_value, array_struct_ptr)

        len_ptr = codegen.types.get_dynamic_array_len_ptr(builder, array_struct_ptr)
        current_len = builder.load(len_ptr, name="array_len")

        data_ptr_ptr = codegen.types.get_dynamic_array_data_ptr(builder, array_struct_ptr)
        data_ptr = builder.load(data_ptr_ptr, name="array_data")

        counter = builder.alloca(i32, name="counter")
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

        element_hash = _emit_element_hash(codegen, element_value, array_type.base_type)

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


def _emit_element_hash(codegen: Any, element_value: ir.Value, element_type: Type) -> ir.Value:
    """Emit code to get the hash of an array element."""
    require_builder(codegen)

    if isinstance(element_type, BuiltinType):
        if element_type == BuiltinType.STRING:
            from sushi_lang.backend.types.primitives.hashing import _emit_string_hash_fnv1a
            return _emit_string_hash_fnv1a(codegen, element_value)

        import sushi_lang.backend.types.primitives.hashing  # noqa: F401

        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    elif isinstance(element_type, StructType):
        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    elif isinstance(element_type, EnumType):
        hash_method = get_builtin_method(element_type, "hash")
        if hash_method is None:
            raise_internal_error("CE0051", type=str(element_type))

        fake_call = MethodCall(
            receiver=Name(id="element", loc=(0, 0)),
            method="hash",
            args=[],
            loc=(0, 0)
        )

        return hash_method.llvm_emitter(
            codegen, fake_call, element_value, element_value.type, False
        )

    else:
        raise_internal_error("CE0052", type=str(element_type))


def emit_fixed_array_hash_direct(codegen: Any, expr: Any, array_ptr: ir.Value,
                                 receiver_type: ir.Type, array_type: Type,
                                 to_i1: bool) -> ir.Value:
    """Direct emitter for fixed array hash.

    The receiver arrives as an address and its type arrives with it. Looking the type up by
    NAME rejected every receiver that has no name, so `b.slots.hash()` on a field was CE0056
    (#480). A wrong element type would silently produce a wrong hash, which is why the old
    code refused rather than guessed; the caller now knows the type, so there is nothing to
    guess.
    """
    emitter = _emit_fixed_array_hash(array_type)
    return emitter(codegen, expr, array_ptr, receiver_type, to_i1)


def emit_dynamic_array_hash_direct(codegen: Any, expr: Any, receiver_value: ir.Value,
                                   receiver_type: ir.Type, to_i1: bool) -> ir.Value:
    """Direct emitter for dynamic array hash (called from backend/expressions/calls.py)."""
    from sushi_lang.semantics.ast import Name

    if isinstance(expr.receiver, Name):
        array_type = codegen.variable_types.get(expr.receiver.id)
        if array_type is None:
            raise_internal_error("CE0056", name=expr.receiver.id)
    else:
        raise_internal_error("CE0056", name=f"<{type(expr.receiver).__name__}>")

    emitter = _emit_dynamic_array_hash(array_type)
    return emitter(codegen, expr, receiver_value, receiver_type, to_i1)


def _make_array_hash_emitter(array_type: Type) -> Any:
    """Build the hash() emitter for an array type, fixed or dynamic."""
    if isinstance(array_type, ArrayType):
        return _emit_fixed_array_hash(array_type)
    if isinstance(array_type, DynamicArrayType):
        return _emit_dynamic_array_hash(array_type)
    raise_internal_error("CE0041", type=type(array_type).__name__)


register_hash_emitter_factory("array", _make_array_hash_emitter)
