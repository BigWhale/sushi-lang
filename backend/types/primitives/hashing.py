"""
Built-in hash extension methods for primitive types.

Implemented methods:
- hash() -> u64: Compute hash value for use in hash tables/collections

All hash functions are optimized for general-purpose hash table usage (not cryptographic):
- Integer types: FxHash mixing for 32/64-bit, identity hash for 8/16-bit
- Float types: Bitcast to integer representation + normalize zero + FxHash mixing
- Boolean: Simple cast to u64 (0 or 1)
- String: FNV-1a algorithm (excellent distribution, single-pass, widely used)

All implementations are pure LLVM IR for optimal performance with full inlining.
"""

from typing import Any
from semantics.ast import MethodCall
from semantics.typesys import BuiltinType, Type
import llvmlite.ir as ir
from backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from internals import errors as er
from internals.errors import raise_internal_error
from backend.utils import require_builder
from stdlib.src.common import register_builtin_method, BuiltinMethod
from backend.types.hash_utils import FNV1A_OFFSET_BASIS, FNV1A_PRIME, emit_fnv1a_combine


# Hash algorithm constants
FXHASH_MULTIPLIER = 0x517cc1b727220a95  # FxHash prime for 64-bit mixing


# Validation function for hash() method
def _validate_hash(call: MethodCall, target_type: Type, reporter: Any) -> None:
    """Validate hash() method call on primitive types."""
    if call.args:
        er.emit(reporter, er.ERR.CE2009, call.loc,
               name=f"{target_type}.hash", expected=0, got=len(call.args))


def _emit_generic_hash(prim_type: BuiltinType) -> Any:
    """Create a hash() emitter function for the given primitive type.

    This factory function generates hash emitters dynamically based on type.

    Args:
        prim_type: The primitive type to create an emitter for.

    Returns:
        An emitter function that computes hash value as u64.
    """
    def emitter(codegen: Any, call: MethodCall, receiver_value: ir.Value,
               receiver_type: ir.Type, to_i1: bool) -> ir.Value:
        """Generic hash() emitter created by factory."""
        if len(call.args) != 0:
            raise_internal_error("CE0054", got=len(call.args))

        builder = require_builder(codegen)
        builder = codegen.builder
        u64 = ir.IntType(INT64_BIT_WIDTH)

        # Dispatch based on type category
        if prim_type in [BuiltinType.I8, BuiltinType.I16, BuiltinType.U8, BuiltinType.U16]:
            # Small integers: identity hash (zero-extend to u64)
            return builder.zext(receiver_value, u64)

        elif prim_type in [BuiltinType.I32, BuiltinType.I64, BuiltinType.U32, BuiltinType.U64]:
            # Large integers: apply FxHash mixing
            # First extend/truncate to u64
            if receiver_value.type.width < 64:
                value_u64 = builder.zext(receiver_value, u64)
            elif receiver_value.type.width > 64:
                value_u64 = builder.trunc(receiver_value, u64)
            else:
                value_u64 = receiver_value

            # FxHash: value * 0x517cc1b727220a95
            multiplier = ir.Constant(u64, FXHASH_MULTIPLIER)
            return builder.mul(value_u64, multiplier)

        elif prim_type in [BuiltinType.F32, BuiltinType.F64]:
            # Float: bitcast to integer + normalize zero + mix
            if prim_type == BuiltinType.F32:
                # f32 -> u32 bitcast -> u64 extend
                u32 = ir.IntType(INT32_BIT_WIDTH)
                bits_u32 = builder.bitcast(receiver_value, u32)

                # Normalize +0.0 and -0.0 to same value
                # Check if value == 0.0 (ignoring sign bit)
                zero_f32 = ir.Constant(ir.FloatType(), 0.0)
                is_zero = builder.fcmp_ordered('==', receiver_value, zero_f32)
                zero_bits = ir.Constant(u32, 0)
                normalized_bits = builder.select(is_zero, zero_bits, bits_u32)

                # Extend to u64
                bits_u64 = builder.zext(normalized_bits, u64)
            else:  # f64
                # f64 -> u64 bitcast
                bits_u64 = builder.bitcast(receiver_value, u64)

                # Normalize +0.0 and -0.0 to same value
                zero_f64 = ir.Constant(ir.DoubleType(), 0.0)
                is_zero = builder.fcmp_ordered('==', receiver_value, zero_f64)
                zero_bits = ir.Constant(u64, 0)
                bits_u64 = builder.select(is_zero, zero_bits, bits_u64)

            # Apply FxHash mixing
            multiplier = ir.Constant(u64, FXHASH_MULTIPLIER)
            return builder.mul(bits_u64, multiplier)

        elif prim_type == BuiltinType.BOOL:
            # Boolean: cast to u64 (0 or 1)
            return builder.zext(receiver_value, u64)

        elif prim_type == BuiltinType.STRING:
            # String: FNV-1a algorithm
            return _emit_string_hash_fnv1a(codegen, receiver_value)

        else:
            raise_internal_error("CE0076", type=prim_type)

    return emitter


def _emit_string_hash_fnv1a(codegen: Any, string_value: ir.Value) -> ir.Value:
    """Emit LLVM IR for FNV-1a string hashing algorithm.

    FNV-1a is a simple, fast, and effective hash function with excellent
    distribution properties for hash tables.

    Algorithm:
        hash = FNV_OFFSET_BASIS
        for each byte in string:
            hash = (hash XOR byte) * FNV_PRIME

    Args:
        codegen: The LLVM code generator
        string_value: Fat pointer string struct {i8*, i32}

    Returns:
        Hash value as u64
    """
    builder = require_builder(codegen)
    builder = codegen.builder
    i8 = ir.IntType(INT8_BIT_WIDTH)
    i32 = ir.IntType(INT32_BIT_WIDTH)
    u64 = ir.IntType(INT64_BIT_WIDTH)

    # Extract pointer and length from fat pointer struct
    string_ptr = builder.extract_value(string_value, 0, name="str_ptr")
    str_len_i32 = builder.extract_value(string_value, 1, name="str_len")
    str_len_u64 = builder.zext(str_len_i32, u64)

    # Initialize hash to FNV offset basis
    hash_value = builder.alloca(u64, name="hash")
    offset_basis = ir.Constant(u64, FNV1A_OFFSET_BASIS)
    builder.store(offset_basis, hash_value)

    # Initialize loop counter
    counter = builder.alloca(u64, name="counter")
    zero_u64 = ir.Constant(u64, 0)
    builder.store(zero_u64, counter)

    # Create loop blocks
    loop_header = builder.append_basic_block(name="hash_loop_header")
    loop_body = builder.append_basic_block(name="hash_loop_body")
    loop_exit = builder.append_basic_block(name="hash_loop_exit")

    # Jump to loop header
    builder.branch(loop_header)

    # Loop header: check if counter < length
    builder.position_at_end(loop_header)
    current_counter = builder.load(counter)
    cond = builder.icmp_unsigned('<', current_counter, str_len_u64)
    builder.cbranch(cond, loop_body, loop_exit)

    # Loop body: process one byte
    builder.position_at_end(loop_body)

    # Load byte at current position
    byte_ptr = builder.gep(string_ptr, [current_counter], inbounds=True)
    byte = builder.load(byte_ptr)
    byte_u64 = builder.zext(byte, u64)

    # Combine using FNV-1a: hash = (hash XOR byte) * FNV_PRIME
    current_hash = builder.load(hash_value)
    new_hash = emit_fnv1a_combine(codegen, current_hash, byte_u64)
    builder.store(new_hash, hash_value)

    # Increment counter
    one_u64 = ir.Constant(u64, 1)
    next_counter = builder.add(current_counter, one_u64)
    builder.store(next_counter, counter)

    # Loop back to header
    builder.branch(loop_header)

    # Loop exit: return final hash
    builder.position_at_end(loop_exit)
    final_hash = builder.load(hash_value)
    return final_hash


# Register hash() methods for all primitive types
primitive_types = [
    BuiltinType.I8, BuiltinType.I16, BuiltinType.I32, BuiltinType.I64,
    BuiltinType.U8, BuiltinType.U16, BuiltinType.U32, BuiltinType.U64,
    BuiltinType.F32, BuiltinType.F64, BuiltinType.BOOL, BuiltinType.STRING
]

# Generate hash emitters dynamically
hash_emitters = {prim_type: _emit_generic_hash(prim_type) for prim_type in primitive_types}

# Register all hash methods
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
