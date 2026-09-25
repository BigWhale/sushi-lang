"""LLVM type helpers and constants for HashMap<K, V>."""

from typing import Any, NamedTuple, Optional
from sushi_lang.semantics.typesys import Type, ArrayType, DynamicArrayType
import llvmlite.ir as ir

from sushi_lang.internals.errors import raise_internal_error

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
    """Get the next power-of-two capacity >= 2 * current."""
    target = current * 2
    for capacity in POWER_OF_TWO_CAPACITIES:
        if capacity >= target:
            return capacity
    return target


def get_entry_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for Entry<K, V>."""
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    state_llvm = codegen.types.i8

    return ir.LiteralStructType([key_llvm, value_llvm, state_llvm])


def get_hashmap_llvm_type(codegen: Any, key_type: Type, value_type: Type) -> ir.Type:
    """Get LLVM struct type for HashMap<K, V>."""
    entry_type = get_entry_type(codegen, key_type, value_type)

    buckets_type = ir.LiteralStructType([
        codegen.types.i32,                    # len
        codegen.types.i32,                    # cap
        ir.PointerType(entry_type)            # data (Entry<K, V>*)
    ])

    return ir.LiteralStructType([
        buckets_type,         # Entry<K, V>[] buckets
        codegen.types.i32,    # i32 size
        codegen.types.i32,    # i32 capacity
        codegen.types.i32,    # i32 tombstones
    ])


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


class _HashCall(NamedTuple):
    """What a derived hash emitter reads from its call: the argument list, which is empty."""
    args: tuple = ()


_HASH_CALL = _HashCall()


def emit_key_hash_i32(codegen: Any, key_type: Type, key_value: ir.Value) -> ir.Value:
    """The probe start of a key: its hash, truncated to i32.

    A perk implementation wins, exactly as it does at the three dispatch layers
    (docs/design/method-resolution.md) -- otherwise the map would probe with the
    derived hash while `.hash()` answers the override.
    """
    hash_value = _emit_perk_key_hash(codegen, key_type, key_value)
    if hash_value is None:
        hash_method = _derived_key_hash_method(codegen, key_type)
        if hash_method is None:
            raise_internal_error("CE0053", type=key_type)
        hash_value = hash_method.llvm_emitter(
            codegen, _HASH_CALL, key_value, codegen.types.ll_type(key_type), False)
    return codegen.builder.trunc(hash_value, codegen.types.i32, name="hash_i32")


def _emit_perk_key_hash(codegen: Any, key_type: Type, key_value: ir.Value) -> Optional[ir.Value]:
    """Call the perk `hash` implementation of the key type, or answer None when it has none."""
    if codegen.perk_impl_table.get_method(key_type, "hash") is None:
        return None

    from sushi_lang.semantics.library_templates import impl_method_symbol
    llvm_fn = codegen.funcs.get(impl_method_symbol(str(key_type), "hash"))
    if llvm_fn is None:
        raise_internal_error("CE0024", method="hash", type=str(key_type))
    param_type = list(llvm_fn.args)[0].type
    return codegen.builder.call(llvm_fn, [codegen.utils.cast_for_param(key_value, param_type)])


def _derived_key_hash_method(codegen: Any, key_type: Type) -> Optional[Any]:
    """The derived hash of a key type, registered on demand for an array key."""
    import sushi_lang.backend.types.primitives.hashing  # noqa: F401

    hash_method = codegen.derived_methods.get_method(key_type, "hash")
    if hash_method is not None:
        return hash_method

    if isinstance(key_type, (ArrayType, DynamicArrayType)):
        from sushi_lang.semantics.generics.hashing import register_hash_if_hashable
        if register_hash_if_hashable(key_type, codegen.derived_methods):
            return codegen.derived_methods.get_method(key_type, "hash")

    return None


def get_user_entry_type(codegen: Any, key_type: Type, value_type: Type) -> 'ir.Type':
    """Get LLVM struct type for the user-facing Entry<K, V> (key + value only)."""
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)
    return ir.LiteralStructType([key_llvm, value_llvm])
