"""
LLVM type helpers and constants for HashMap<K, V>.

This module provides functions to create LLVM struct types for HashMap<K, V>
and Entry<K, V>, along with constants for entry states and capacity tables.

The ir-free half -- method validation, and resolving K/V out of a monomorphized
"HashMap<K, V>" name -- lives in `semantics/generics/hashmap.py`.
"""

from typing import Any, NamedTuple, Optional
from sushi_lang.semantics.typesys import Type, ArrayType, DynamicArrayType
import llvmlite.ir as ir

from sushi_lang.backend.constants import (
    HASHMAP_BUCKETS_INDICES,
    HASHMAP_SIZE_INDICES,
    HASHMAP_CAPACITY_INDICES,
    HASHMAP_TOMBSTONES_INDICES,
    BUCKETS_DATA_INDICES,
)


# ==============================================================================
# Entry State Constants
# ==============================================================================

ENTRY_EMPTY = 0      # Slot is empty (never used)
ENTRY_OCCUPIED = 1   # Slot contains valid key-value pair
ENTRY_TOMBSTONE = 2  # Slot was deleted (marks probe chain)


# ==============================================================================
# Power-of-Two Capacity Table for Resize
# ==============================================================================

# Power-of-two capacities for HashMap (fast bitwise AND indexing)
# With strong hash functions (FxHash, FNV-1a), power-of-two provides:
# - 3-10x faster indexing (AND vs modulo)
# - Excellent distribution (hash quality matters, not capacity)
# - Simpler implementation
POWER_OF_TWO_CAPACITIES = [
    16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
    16384, 32768, 65536, 131072, 262144, 524288, 1048576,
    2097152, 4194304, 8388608, 16777216
]


def get_next_capacity(current: int) -> int:
    """Get the next power-of-two capacity >= 2 * current.

    Args:
        current: Current capacity.

    Returns:
        Next power-of-two >= 2 * current.
    """
    target = current * 2
    for capacity in POWER_OF_TWO_CAPACITIES:
        if capacity >= target:
            return capacity
    # If we exceed our table, just double (still power-of-two)
    return target


# ==============================================================================
# LLVM Type Constructors
# ==============================================================================


def get_entry_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for Entry<K, V>.

    Structure:
        struct Entry<K, V>:
            K key
            V value
            u8 state  # 0=Empty, 1=Occupied, 2=Tombstone

    Args:
        codegen: LLVM codegen instance.
        key_type: The key type K.
        value_type: The value type V.

    Returns:
        LLVM literal struct type for Entry<K, V>.
    """
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    state_llvm = codegen.types.i8

    return ir.LiteralStructType([key_llvm, value_llvm, state_llvm])


def get_hashmap_llvm_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for HashMap<K, V>.

    Structure:
        struct HashMap<K, V>:
            Entry<K, V>[] buckets  # Dynamic array: {i32 len, i32 cap, Entry* data}
            i32 size
            i32 capacity
            i32 tombstones

    Args:
        codegen: LLVM codegen instance.
        key_type: The key type K.
        value_type: The value type V.

    Returns:
        LLVM literal struct type for HashMap<K, V>.
    """
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Dynamic array of Entry<K, V>: {i32 len, i32 cap, Entry* data}
    buckets_type = ir.LiteralStructType([
        codegen.types.i32,                    # len
        codegen.types.i32,                    # cap
        ir.PointerType(entry_type)            # data (Entry<K, V>*)
    ])

    # HashMap struct: {buckets, size, capacity, tombstones}
    return ir.LiteralStructType([
        buckets_type,         # Entry<K, V>[] buckets
        codegen.types.i32,    # i32 size
        codegen.types.i32,    # i32 capacity
        codegen.types.i32,    # i32 tombstones
    ])


# ==============================================================================
# Field Access
# ==============================================================================


class HashMapFields(NamedTuple):
    """Pointers to the fields of a HashMap<K, V> struct.

    `buckets_data` points at the dynamic array's data *field*, not at the bucket
    storage -- load it to get the Entry<K, V>* itself.
    """
    buckets_data: ir.Value
    size: ir.Value
    capacity: ir.Value
    tombstones: ir.Value


def get_hashmap_field_ptrs(codegen: Any, hashmap_ptr: ir.Value) -> HashMapFields:
    """GEP the four HashMap<K, V> fields at once.

    Every HashMap method opens by reaching for some subset of these, so they are
    computed together rather than re-deriving the struct layout at each use.

    Args:
        codegen: LLVM codegen instance.
        hashmap_ptr: Pointer to the HashMap struct.

    Returns:
        Pointers to buckets.data, size, capacity and tombstones.
    """
    builder = codegen.builder
    buckets_ptr = builder.gep(hashmap_ptr, HASHMAP_BUCKETS_INDICES, name="buckets_ptr")
    return HashMapFields(
        buckets_data=builder.gep(buckets_ptr, BUCKETS_DATA_INDICES, name="buckets_data_ptr"),
        size=builder.gep(hashmap_ptr, HASHMAP_SIZE_INDICES, name="size_ptr"),
        capacity=builder.gep(hashmap_ptr, HASHMAP_CAPACITY_INDICES, name="capacity_ptr"),
        tombstones=builder.gep(hashmap_ptr, HASHMAP_TOMBSTONES_INDICES, name="tombstones_ptr"),
    )


# ==============================================================================
# Key Hashing
# ==============================================================================


def get_key_hash_method(key_type: Type) -> Optional[Any]:
    """Get the hash method for a HashMap key type, registering it on-demand if needed.

    This function handles on-demand hash registration for array types used as HashMap keys.
    Array types may not be registered in Pass 1.8 if they only appear as HashMap type
    parameters (not in struct/enum fields).

    Args:
        key_type: The key type (must have a hash() method).

    Returns:
        The BuiltinMethod for hash(), or None if the type cannot be hashed.
    """
    from sushi_lang.sushi_stdlib.src.common import get_builtin_method

    # Try to get existing hash method
    hash_method = get_builtin_method(key_type, "hash")
    if hash_method is not None:
        return hash_method

    # For array types, try to register on-demand
    if isinstance(key_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.generics.hashing import register_array_hash_method, can_array_be_hashed
        can_hash, reason = can_array_be_hashed(key_type)
        if can_hash:
            register_array_hash_method(key_type)
            return get_builtin_method(key_type, "hash")

    return None


def get_user_entry_type(codegen: Any, key_type: Type, value_type: Type) -> 'ir.Type':
    """Get LLVM struct type for the user-facing Entry<K, V> (key + value only).

    Args:
        codegen: LLVM codegen instance.
        key_type: The key type K.
        value_type: The value type V.

    Returns:
        LLVM literal struct type for user-facing Entry<K, V>.
    """
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    return ir.LiteralStructType([key_llvm, value_llvm])
