"""Shared utilities for hash function implementation."""

import llvmlite.ir as ir
from sushi_lang.backend.constants import INT64_BIT_WIDTH
from typing import TYPE_CHECKING
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# FNV-1a Hash Algorithm Constants (64-bit)
# These constants are used for consistent hashing across all types
FNV1A_OFFSET_BASIS = 14695981039346656037  # FNV-1a offset basis (64-bit)
FNV1A_PRIME = 1099511628211  # FNV-1a prime (64-bit)


def emit_fnv1a_combine(codegen: 'LLVMCodegen', current_hash: ir.Value, value_hash: ir.Value) -> ir.Value:
    """Emit LLVM IR to combine a hash value using FNV-1a algorithm."""
    builder = require_builder(codegen)
    builder = codegen.builder
    u64 = ir.IntType(INT64_BIT_WIDTH)

    # hash = hash XOR value_hash
    xor_result = builder.xor(current_hash, value_hash)

    # hash = hash * FNV_PRIME
    prime = ir.Constant(u64, FNV1A_PRIME)
    combined = builder.mul(xor_result, prime)

    return combined


def emit_fnv1a_init(codegen: 'LLVMCodegen') -> ir.Value:
    """Emit LLVM IR to initialize FNV-1a hash with offset basis."""
    u64 = ir.IntType(INT64_BIT_WIDTH)
    return ir.Constant(u64, FNV1A_OFFSET_BASIS)
