"""
Shared utilities for hash function implementation.

This module provides common constants and helper functions used across
different hash implementations (primitives, structs, etc.).
"""

import llvmlite.ir as ir
from sushi_lang.backend.constants import INT8_BIT_WIDTH, INT32_BIT_WIDTH, INT64_BIT_WIDTH
from typing import TYPE_CHECKING
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.utils import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen


# FNV-1a Hash Algorithm Constants (64-bit)
# These constants are used for consistent hashing across all types
FNV1A_OFFSET_BASIS = 14695981039346656037  # FNV-1a offset basis (64-bit)
FNV1A_PRIME = 1099511628211  # FNV-1a prime (64-bit)


def emit_fnv1a_combine(codegen: 'LLVMCodegen', current_hash: ir.Value, value_hash: ir.Value) -> ir.Value:
    """Emit LLVM IR to combine a hash value using FNV-1a algorithm.

    FNV-1a combination:
        hash = (hash XOR value_hash) * FNV_PRIME

    This is the core operation for combining multiple hash values together,
    used for both string hashing and struct field hashing.

    Args:
        codegen: The LLVM code generator
        current_hash: Current accumulated hash value (u64)
        value_hash: New hash value to combine (u64)

    Returns:
        Combined hash value (u64)
    """
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
    """Emit LLVM IR to initialize FNV-1a hash with offset basis.

    Returns:
        Initial hash value (u64) set to FNV_OFFSET_BASIS
    """
    u64 = ir.IntType(INT64_BIT_WIDTH)
    return ir.Constant(u64, FNV1A_OFFSET_BASIS)
