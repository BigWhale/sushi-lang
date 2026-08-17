"""Built-in hash extension methods for primitive types."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import BuiltinType, Type
import llvmlite.ir as ir
from sushi_lang.backend.constants import INT32_BIT_WIDTH, INT64_BIT_WIDTH
from sushi_lang.internals import errors as er
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder
from sushi_lang.sushi_stdlib.src.common import register_builtin_method, BuiltinMethod
from sushi_lang.backend.types.hash_utils import FNV1A_OFFSET_BASIS, emit_fnv1a_combine
from sushi_lang.semantics.generics.type_display import display_type


FXHASH_MULTIPLIER = 0x517cc1b727220a95  # FxHash prime for 64-bit mixing


def _validate_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on primitive types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{display_type(target_type)}.hash", expected=0, got=len(call.args))


def _emit_generic_hash(prim_type: BuiltinType) -> Any:
    """Create a hash() emitter function for the given primitive type."""
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Generic hash() emitter created by factory."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)

        if prim_type in [BuiltinType.I8, BuiltinType.I16, BuiltinType.U8, BuiltinType.U16]:
            return builder.zext(receiver_value, u64)

        elif prim_type in [BuiltinType.I32, BuiltinType.I64, BuiltinType.U32, BuiltinType.U64]:
            if receiver_value.type.width < 64:
                value_u64 = builder.zext(receiver_value, u64)
            elif receiver_value.type.width > 64:
                value_u64 = builder.trunc(receiver_value, u64)
            else:
                value_u64 = receiver_value

            multiplier = ir.Constant(u64, FXHASH_MULTIPLIER)
            return builder.mul(value_u64, multiplier)

        elif prim_type in [BuiltinType.F32, BuiltinType.F64]:
            if prim_type == BuiltinType.F32:
                u32 = ir.IntType(INT32_BIT_WIDTH)
                bits_u32 = builder.bitcast(receiver_value, u32)

                zero_f32 = ir.Constant(ir.FloatType(), 0.0)
                is_zero = builder.fcmp_ordered('==', receiver_value, zero_f32)
                zero_bits = ir.Constant(u32, 0)
                normalized_bits = builder.select(is_zero, zero_bits, bits_u32)

                bits_u64 = builder.zext(normalized_bits, u64)
            else:  # f64
                bits_u64 = builder.bitcast(receiver_value, u64)

                zero_f64 = ir.Constant(ir.DoubleType(), 0.0)
                is_zero = builder.fcmp_ordered('==', receiver_value, zero_f64)
                zero_bits = ir.Constant(u64, 0)
                bits_u64 = builder.select(is_zero, zero_bits, bits_u64)

            multiplier = ir.Constant(u64, FXHASH_MULTIPLIER)
            return builder.mul(bits_u64, multiplier)

        elif prim_type == BuiltinType.BOOL:
            return builder.zext(receiver_value, u64)

        elif prim_type == BuiltinType.STRING:
            return _emit_string_hash_fnv1a(codegen, receiver_value)

        else:
            raise_internal_error("CE0076", type=prim_type)

    return emitter


def _emit_string_hash_fnv1a(codegen: Any, string_value: ir.Value) -> ir.Value:
    """Emit LLVM IR for FNV-1a string hashing algorithm."""
    builder = require_builder(codegen)
    builder = codegen.builder
    u64 = ir.IntType(INT64_BIT_WIDTH)

    string_ptr = builder.extract_value(string_value, 0, name="str_ptr")
    str_len_i32 = builder.extract_value(string_value, 1, name="str_len")
    str_len_u64 = builder.zext(str_len_i32, u64)

    hash_value = builder.alloca(u64, name="hash")
    offset_basis = ir.Constant(u64, FNV1A_OFFSET_BASIS)
    builder.store(offset_basis, hash_value)

    counter = builder.alloca(u64, name="counter")
    zero_u64 = ir.Constant(u64, 0)
    builder.store(zero_u64, counter)

    loop_header = builder.append_basic_block(name="hash_loop_header")
    loop_body = builder.append_basic_block(name="hash_loop_body")
    loop_exit = builder.append_basic_block(name="hash_loop_exit")

    builder.branch(loop_header)

    builder.position_at_end(loop_header)
    current_counter = builder.load(counter)
    cond = builder.icmp_unsigned('<', current_counter, str_len_u64)
    builder.cbranch(cond, loop_body, loop_exit)

    builder.position_at_end(loop_body)

    byte_ptr = builder.gep(string_ptr, [current_counter], inbounds=True)
    byte = builder.load(byte_ptr)
    byte_u64 = builder.zext(byte, u64)

    current_hash = builder.load(hash_value)
    new_hash = emit_fnv1a_combine(codegen, current_hash, byte_u64)
    builder.store(new_hash, hash_value)

    one_u64 = ir.Constant(u64, 1)
    next_counter = builder.add(current_counter, one_u64)
    builder.store(next_counter, counter)

    builder.branch(loop_header)

    builder.position_at_end(loop_exit)
    final_hash = builder.load(hash_value)
    return final_hash


primitive_types = [
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
]

hash_emitters = {prim_type: _emit_generic_hash(prim_type) for prim_type in primitive_types}

for prim_type in primitive_types:
    register_builtin_method(
        prim_type,
        BuiltinMethod(
            name="hash",
            parameter_types=[],
            return_type=BuiltinType.U64,
            description=f"Compute hash value for {prim_type}",
            semantic_validator=_validate_hash,
            llvm_emitter=hash_emitters[prim_type],
        )
    )
