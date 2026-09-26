"""LLVM type helpers and constants for HashMap<K, V>."""

from typing import Any, NamedTuple
from sushi_lang.semantics.typesys import Type
import llvmlite.ir as ir


from sushi_lang.backend.constants import (
    HASHMAP_BUCKETS_INDICES,
    HASHMAP_SIZE_INDICES,
    HASHMAP_CAPACITY_INDICES,
    HASHMAP_TOMBSTONES_INDICES,
    BUCKETS_DATA_INDICES,
)


ENTRY_EMPTY = 0      # Slot is empty (never used)
ENTRY_OCCUPIED = 1   # Slot contains valid key-value pair
ENTRY_TOMBSTONE = 2  # Slot was deleted (marks probe chain)


def get_entry_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for Entry<K, V>."""
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    state_llvm = codegen.types.i8

    return ir.LiteralStructType([key_llvm, value_llvm, state_llvm])


class HashMapFields(NamedTuple):
    """Pointers to the fields of a HashMap<K, V> struct."""
    buckets_data: ir.Value
    size: ir.Value
    capacity: ir.Value
    tombstones: ir.Value


def get_hashmap_field_ptrs(codegen: Any, hashmap_ptr: ir.Value) -> HashMapFields:
    """GEP the four HashMap<K, V> fields at once."""
    builder = codegen.builder
    buckets_ptr = builder.gep(hashmap_ptr, HASHMAP_BUCKETS_INDICES, name="buckets_ptr")
    return HashMapFields(
        buckets_data=builder.gep(buckets_ptr, BUCKETS_DATA_INDICES, name="buckets_data_ptr"),
        size=builder.gep(hashmap_ptr, HASHMAP_SIZE_INDICES, name="size_ptr"),
        capacity=builder.gep(hashmap_ptr, HASHMAP_CAPACITY_INDICES, name="capacity_ptr"),
        tombstones=builder.gep(hashmap_ptr, HASHMAP_TOMBSTONES_INDICES, name="tombstones_ptr"),
    )


def emit_key_hash_i32(codegen: Any, key_type: Type, key_value: ir.Value) -> ir.Value:
    """The probe start of a key: its hash, truncated to i32.

    A key is a held value, so it hashes through `emit_value_hash`: a perk
    implementation wins, exactly as it does for `.hash()` and in every other held
    position -- otherwise the map would probe with the derived hash while `.hash()`
    answers the override.
    """
    from sushi_lang.backend.types.value_hash import emit_value_hash

    hash_value = emit_value_hash(codegen, key_value, key_type)
    return codegen.builder.trunc(hash_value, codegen.types.i32, name="hash_i32")


def get_user_entry_type(codegen: Any, key_type: Type, value_type: Type) -> 'ir.Type':
    """Get LLVM struct type for the user-facing Entry<K, V> (key + value only)."""
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    return ir.LiteralStructType([key_llvm, value_llvm])
