"""HashMap<K, V> core method implementations."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir
from ..types import get_entry_type
from sushi_lang.backend.constants import (
    HASHMAP_BUCKETS_INDICES,
    HASHMAP_SIZE_INDICES,
    HASHMAP_CAPACITY_INDICES,
    HASHMAP_TOMBSTONES_INDICES,
    BUCKETS_DATA_INDICES,
    ENTRY_VALUE_INDICES,
)
from sushi_lang.semantics.generics.hashmap import parse_hashmap_types
from ..utils import emit_init_buckets_empty
from ..probe import emit_find_key, emit_lookup_key, emit_lookup_maybe
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.expressions.memory import get_element_size_constant


def emit_hashmap_new(codegen: Any, hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.new() -> HashMap<K, V>"""
    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")

    entry_type = get_entry_type(codegen, key_type, value_type)
    hashmap_llvm_type = codegen.types.ll_type(hashmap_type)

    initial_capacity = 16
    capacity_const = ir.Constant(codegen.types.i32, initial_capacity)

    entry_size = get_element_size_constant(codegen, entry_type)
    total_bytes = codegen.builder.mul(entry_size, capacity_const, name="bucket_bytes")

    total_bytes_i64 = codegen.builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    bucket_ptr_i8 = emit_malloc(codegen, codegen.builder, total_bytes_i64)
    bucket_ptr = codegen.builder.bitcast(bucket_ptr_i8, ir.PointerType(entry_type), name="buckets_ptr")

    zero_i32 = ir.Constant(codegen.types.i32, 0)
    emit_init_buckets_empty(codegen, bucket_ptr, capacity_const)

    buckets_array_type = ir.LiteralStructType([codegen.types.i32, codegen.types.i32, ir.PointerType(entry_type)])
    buckets_array = ir.Constant(buckets_array_type, ir.Undefined)
    buckets_array = codegen.builder.insert_value(buckets_array, capacity_const, 0, name="buckets_len")
    buckets_array = codegen.builder.insert_value(buckets_array, capacity_const, 1, name="buckets_cap")
    buckets_array = codegen.builder.insert_value(buckets_array, bucket_ptr, 2, name="buckets_data")

    result = ir.Constant(hashmap_llvm_type, ir.Undefined)
    result = codegen.builder.insert_value(result, buckets_array, 0, name="hm_buckets")
    result = codegen.builder.insert_value(result, zero_i32, 1, name="hm_size")
    result = codegen.builder.insert_value(result, capacity_const, 2, name="hm_capacity")
    result = codegen.builder.insert_value(result, zero_i32, 3, name="hm_tombstones")

    return result


def emit_hashmap_len(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.len() -> i32"""
    builder = codegen.builder
    size_ptr = builder.gep(hashmap_value, HASHMAP_SIZE_INDICES, name="size_ptr")
    return builder.load(size_ptr, name="hashmap_size")


def emit_hashmap_is_empty(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.is_empty() -> bool"""
    size = emit_hashmap_len(codegen, hashmap_value)
    zero = ir.Constant(codegen.types.i32, 0)
    return codegen.builder.icmp_signed("==", size, zero, name="is_empty")


def emit_hashmap_tombstone_count(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.tombstone_count() -> i32"""
    builder = codegen.builder
    tombstones_ptr = builder.gep(hashmap_value, HASHMAP_TOMBSTONES_INDICES, name="tombstones_ptr")
    return builder.load(tombstones_ptr, name="hashmap_tombstones")


def emit_hashmap_get(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.get(K key) -> Maybe<V>"""
    builder = codegen.builder

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    key_value = emit_lookup_key(codegen, expr, key_type, "get")
    capacity, buckets_data = _load_capacity_and_buckets(builder, hashmap_value)

    lookup = emit_find_key(codegen, buckets_data, capacity, key_type, key_value, "get")

    builder.position_at_end(lookup.found)
    entry_value_ptr = builder.gep(lookup.entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
    entry_value = builder.load(entry_value_ptr, name="entry_value")

    # `.get()` READS. It does not detach (#242): the entry stays OCCUPIED and `map.free()`
    # still destroys it, so the returned `Maybe.Some(V)` carries a BORROW. The borrow pass
    # classifies it BORROWED, a `let` of it binds without owning, and a position that
    # takes ownership rejects it (CE2411) with `.clone()` as the escape. The deep copy
    # that used to happen here was the compiler inserting one the user did not ask for.
    return emit_lookup_maybe(codegen, lookup, value_type, entry_value, "get_result")


def emit_hashmap_contains_key(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.contains_key(K key) -> bool"""
    builder = codegen.builder

    key_type, _ = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    key_value = emit_lookup_key(codegen, expr, key_type, "contains_key")
    capacity, buckets_data = _load_capacity_and_buckets(builder, hashmap_value)

    lookup = emit_find_key(codegen, buckets_data, capacity, key_type, key_value, "contains")

    builder.position_at_end(lookup.found)
    builder.branch(lookup.done)

    builder.position_at_end(lookup.not_found)
    builder.branch(lookup.done)

    builder.position_at_end(lookup.done)
    result_phi = builder.phi(codegen.types.i32, name="contains_result")
    result_phi.add_incoming(ir.Constant(codegen.types.i32, 1), lookup.found)
    result_phi.add_incoming(ir.Constant(codegen.types.i32, 0), lookup.not_found)

    return result_phi


def _load_capacity_and_buckets(builder: ir.IRBuilder, hashmap_value: ir.Value) -> tuple[ir.Value, ir.Value]:
    """Load the capacity and the bucket data pointer of a map, for a read-only lookup."""
    capacity_ptr = builder.gep(hashmap_value, HASHMAP_CAPACITY_INDICES, name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    buckets_ptr = builder.gep(hashmap_value, HASHMAP_BUCKETS_INDICES, name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, BUCKETS_DATA_INDICES, name="buckets_data_ptr")
    return capacity, builder.load(buckets_data_ptr, name="buckets_data")
