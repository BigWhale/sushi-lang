"""HashMap<K, V> mutation method implementations."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir
from ..types import HashMapFields, get_entry_type, get_hashmap_field_ptrs, emit_key_hash_i32, ENTRY_OCCUPIED, ENTRY_TOMBSTONE
from sushi_lang.backend.constants import (
    HASHMAP_CAPACITY_INDICES,
    ENTRY_KEY_INDICES,
    ENTRY_VALUE_INDICES,
    ENTRY_STATE_INDICES,
)
from sushi_lang.semantics.generics.hashmap import parse_hashmap_types
from ..probe import emit_find_key, emit_lookup_key, emit_lookup_maybe, emit_probe_loop, ProbeSlot
from ..utils import (
    emit_key_equality_check,
    emit_insert_entry,
    emit_destroy_all_entries,
    emit_init_buckets_empty,
)
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.expressions.memory import get_element_size_constant
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.destructors import destroy_old_value


def emit_hashmap_insert(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.insert(K key, V value) -> ~"""
    builder = codegen.builder

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")

    entry_type = get_entry_type(codegen, key_type, value_type)

    one_i32 = ir.Constant(codegen.types.i32, 1)

    if len(expr.args) != 2:
        raise_internal_error("CE0023", method="insert", expected=2, got=len(expr.args))

    # BOTH the key and the value are stored shallowly, so the map takes ownership of each
    # and frees them on `.free()`/scope exit -- two consuming uses, not one. The key was
    # the half that got overlooked, so a heap-owning key (a string bound out of a split()
    # array, say) was freed by its own RAII and by the map.
    from sushi_lang.backend.ownership import ConsumingUse, consume
    key_value = consume(codegen, expr.args[0],
                        codegen.expressions.emit_expr(expr.args[0]),
                        key_type, ConsumingUse.CONTAINER_INSERT)
    value_value = consume(codegen, expr.args[1],
                          codegen.expressions.emit_expr(expr.args[1]),
                          value_type, ConsumingUse.CONTAINER_INSERT)

    # Get pointers to HashMap fields
    # hashmap_value should be a POINTER to the HashMap struct (like array_value for arrays)
    # HashMap struct: {buckets, size, capacity, tombstones}
    fields = get_hashmap_field_ptrs(codegen, hashmap_value)
    size_ptr, capacity_ptr = fields.size, fields.capacity
    tombstones_ptr, buckets_data_ptr = fields.tombstones, fields.buckets_data

    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Check load factor and resize if needed
    # Load factor = (size + tombstones) / capacity
    # If load factor > 0.75, resize to next power-of-two capacity
    # We use integer arithmetic: (size + tombstones) * 4 > capacity * 3
    size_plus_tombstones = builder.add(size, tombstones, name="size_plus_tombstones")
    lhs = builder.mul(size_plus_tombstones, ir.Constant(codegen.types.i32, 4), name="lhs")
    rhs = builder.mul(capacity, ir.Constant(codegen.types.i32, 3), name="rhs")
    should_resize = builder.icmp_unsigned(">", lhs, rhs, name="should_resize")

    resize_bb = builder.append_basic_block(name="resize_hashmap")
    continue_insert_bb = builder.append_basic_block(name="continue_insert")
    builder.cbranch(should_resize, resize_bb, continue_insert_bb)

    builder.position_at_end(resize_bb)
    new_capacity = builder.shl(capacity, ir.Constant(codegen.types.i32, 1), name="new_capacity")

    emit_hashmap_resize_to_capacity(codegen, hashmap_value, hashmap_type, new_capacity)
    builder.branch(continue_insert_bb)

    builder.position_at_end(continue_insert_bb)
    capacity = builder.load(capacity_ptr, name="capacity_current")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data_current")

    hash_i32 = emit_key_hash_i32(codegen, key_type, key_value)

    insert_done_bb = builder.append_basic_block(name="insert_done")

    # Loop-carried across probe steps: the first tombstone this chain passed, or
    # -1. The key may still be live further along the chain, so a tombstone cannot
    # end the probe -- but if the chain runs out, that slot is where the key goes.
    first_tombstone_idx = entry_alloca(builder, codegen.types.i32, name="first_tombstone_idx")
    no_tombstone = ir.Constant(codegen.types.i32, -1)
    builder.store(no_tombstone, first_tombstone_idx)

    def on_occupied(slot: ProbeSlot) -> None:
        entry_key_ptr = builder.gep(slot.entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
        entry_key = builder.load(entry_key_ptr, name="entry_key")
        keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)

        update_value_bb = builder.append_basic_block(name="update_value")
        builder.cbranch(keys_equal, update_value_bb, slot.continue_bb)

        builder.position_at_end(update_value_bb)
        # An update REPLACES two owned values, not one. Both arguments were consumed
        # above, so the map owns the incoming key and the incoming value; the entry
        # already holds a key and a value it owns as well. Destroy each old half before
        # its store, exactly as `_store_fill_element` does for an array slot -- without
        # it the overwritten value leaked, and so did the key the call consumed (#591).
        # The key equality check above has already read the old key.
        destroy_old_value(codegen, entry_key_ptr, key_type)
        builder.store(key_value, entry_key_ptr)

        entry_value_ptr = builder.gep(slot.entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
        destroy_old_value(codegen, entry_value_ptr, value_type)
        builder.store(value_value, entry_value_ptr)
        builder.branch(insert_done_bb)

    def on_tombstone(slot: ProbeSlot) -> None:
        first_tombstone = builder.load(first_tombstone_idx, name="first_tombstone")
        is_first = builder.icmp_signed("==", first_tombstone, no_tombstone, name="is_first_tombstone")

        record_tombstone_bb = builder.append_basic_block(name="record_tombstone")
        builder.cbranch(is_first, record_tombstone_bb, slot.continue_bb)

        builder.position_at_end(record_tombstone_bb)
        builder.store(slot.index, first_tombstone_idx)
        builder.branch(slot.continue_bb)

    def on_empty(slot: ProbeSlot) -> None:
        first_tombstone = builder.load(first_tombstone_idx, name="first_tombstone_final")
        has_tombstone = builder.icmp_signed("!=", first_tombstone, no_tombstone, name="has_tombstone")

        use_tombstone_bb = builder.append_basic_block(name="use_tombstone")
        use_empty_bb = builder.append_basic_block(name="use_empty")
        builder.cbranch(has_tombstone, use_tombstone_bb, use_empty_bb)

        builder.position_at_end(use_tombstone_bb)
        tombstone_entry_ptr = builder.gep(buckets_data, [first_tombstone], name="tombstone_entry_ptr")
        emit_insert_entry(codegen, tombstone_entry_ptr, key_value, value_value, entry_type)
        builder.store(builder.add(size, one_i32, name="new_size"), size_ptr)
        builder.store(builder.sub(tombstones, one_i32, name="new_tombstones"), tombstones_ptr)
        builder.branch(insert_done_bb)

        builder.position_at_end(use_empty_bb)
        emit_insert_entry(codegen, slot.entry_ptr, key_value, value_value, entry_type)
        builder.store(builder.add(size, one_i32, name="new_size"), size_ptr)
        builder.branch(insert_done_bb)

    # A live map always resizes below a 0.75 load factor, so there is always an
    # empty slot and the probe always ends. A DESTROYED map has capacity 0 and null
    # buckets, and reaches here: without the guard the probe masks the hash with
    # -1 and GEPs off the null pointer. Trap instead of corrupting memory.
    no_slot_bb = builder.append_basic_block(name="insert_no_slot")

    emit_probe_loop(
        codegen, buckets_data, capacity, hash_i32,
        on_occupied=on_occupied, on_empty=on_empty, on_tombstone=on_tombstone,
        exhausted_bb=no_slot_bb, prefix="probe",
    )

    builder.position_at_end(no_slot_bb)
    codegen.runtime.errors.emit_runtime_error("RE2022")
    builder.unreachable()

    builder.position_at_end(insert_done_bb)

    return ir.Constant(codegen.types.i32, 0)


def emit_hashmap_remove(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.remove(K key) -> Maybe<V>"""
    builder = codegen.builder

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    one_i32 = ir.Constant(codegen.types.i32, 1)
    key_value = emit_lookup_key(codegen, expr, key_type, "remove")

    fields = get_hashmap_field_ptrs(codegen, hashmap_value)
    size_ptr, capacity_ptr = fields.size, fields.capacity
    tombstones_ptr, buckets_data_ptr = fields.tombstones, fields.buckets_data

    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    lookup = emit_find_key(codegen, buckets_data, capacity, key_type, key_value, "remove")

    builder.position_at_end(lookup.found)
    state_ptr = builder.gep(lookup.entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")

    entry_value_ptr = builder.gep(lookup.entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
    entry_value = builder.load(entry_value_ptr, name="entry_value")

    # Destroy the removed key (the map owned it); the value is moved out into the
    # returned Maybe.Some, so it must NOT be destroyed. Without this, a heap-owning
    # key (e.g. a string) is leaked when its entry becomes a tombstone.
    from sushi_lang.backend.destructors import emit_value_destructor
    emit_value_destructor(codegen, lookup.entry_key_ptr, key_type)

    builder.store(ir.Constant(codegen.types.i8, ENTRY_TOMBSTONE), state_ptr)

    new_size = builder.sub(size, one_i32, name="new_size")
    builder.store(new_size, size_ptr)
    new_tombstones = builder.add(tombstones, one_i32, name="new_tombstones")
    builder.store(new_tombstones, tombstones_ptr)

    return emit_lookup_maybe(codegen, lookup, value_type, entry_value, "remove_result")


def emit_hashmap_resize_to_capacity(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType,
    new_capacity: ir.Value
) -> None:
    """Internal helper: resize HashMap to a specific capacity."""
    builder = codegen.builder

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")

    entry_type = get_entry_type(codegen, key_type, value_type)

    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)

    fields = get_hashmap_field_ptrs(codegen, hashmap_value)
    capacity_ptr = fields.capacity
    tombstones_ptr, buckets_data_ptr = fields.tombstones, fields.buckets_data

    old_capacity = builder.load(capacity_ptr, name="old_capacity")

    old_buckets_data = builder.load(buckets_data_ptr, name="old_buckets_data")

    entry_size = get_element_size_constant(codegen, entry_type)
    total_bytes = builder.mul(entry_size, new_capacity, name="bucket_bytes")

    total_bytes_i64 = builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    new_bucket_ptr_i8 = emit_malloc(codegen, builder, total_bytes_i64)
    new_bucket_ptr = builder.bitcast(new_bucket_ptr_i8, ir.PointerType(entry_type), name="new_buckets_ptr")

    emit_init_buckets_empty(codegen, new_bucket_ptr, new_capacity)

    old_i = entry_alloca(builder, codegen.types.i32, name="old_i")
    builder.store(zero_i32, old_i)

    rehash_loop_cond_bb = builder.append_basic_block(name="rehash_loop_cond")
    rehash_loop_body_bb = builder.append_basic_block(name="rehash_loop_body")
    rehash_check_occupied_bb = builder.append_basic_block(name="rehash_check_occupied")
    rehash_reinsert_bb = builder.append_basic_block(name="rehash_reinsert")
    rehash_skip_bb = builder.append_basic_block(name="rehash_skip")
    rehash_loop_end_bb = builder.append_basic_block(name="rehash_loop_end")

    builder.branch(rehash_loop_cond_bb)

    builder.position_at_end(rehash_loop_cond_bb)
    old_i_val = builder.load(old_i, name="old_i_val")
    rehash_cond = builder.icmp_unsigned("<", old_i_val, old_capacity, name="rehash_cond")
    builder.cbranch(rehash_cond, rehash_loop_body_bb, rehash_loop_end_bb)

    builder.position_at_end(rehash_loop_body_bb)
    old_i_val = builder.load(old_i, name="old_i_val")
    old_entry_ptr = builder.gep(old_buckets_data, [old_i_val], name="old_entry_ptr")
    old_state_ptr = builder.gep(old_entry_ptr, ENTRY_STATE_INDICES, name="old_state_ptr")
    old_state = builder.load(old_state_ptr, name="old_state")

    builder.branch(rehash_check_occupied_bb)

    builder.position_at_end(rehash_check_occupied_bb)
    is_occupied = builder.icmp_unsigned("==", old_state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    builder.cbranch(is_occupied, rehash_reinsert_bb, rehash_skip_bb)

    builder.position_at_end(rehash_reinsert_bb)

    old_key_ptr = builder.gep(old_entry_ptr, ENTRY_KEY_INDICES, name="old_key_ptr")
    old_key = builder.load(old_key_ptr, name="old_key")
    old_value_ptr = builder.gep(old_entry_ptr, ENTRY_VALUE_INDICES, name="old_value_ptr")
    old_value = builder.load(old_value_ptr, name="old_value")

    hash_i32 = emit_key_hash_i32(codegen, key_type, old_key)

    # Linear probe for an empty slot in the NEW buckets. A rehash never collides
    # with an equal key (the old table had none) and the new table has no
    # tombstones, so only the empty case does anything -- and it exits into the
    # enclosing rehash loop's continue block rather than out of the function.
    def on_empty(slot: ProbeSlot) -> None:
        emit_insert_entry(codegen, slot.entry_ptr, old_key, old_value, entry_type)
        builder.branch(rehash_skip_bb)

    rehash_no_slot_bb = builder.append_basic_block(name="rehash_no_slot")

    emit_probe_loop(
        codegen, new_bucket_ptr, new_capacity, hash_i32,
        on_empty=on_empty, exhausted_bb=rehash_no_slot_bb, prefix="rehash_probe",
    )

    # The new table is freshly allocated and strictly larger than the live entry
    # count, so it always has room. Unreachable in a correct compiler; guarded so a
    # bug here cannot become an unbounded loop.
    builder.position_at_end(rehash_no_slot_bb)
    codegen.runtime.errors.emit_runtime_error("RE2022")
    builder.unreachable()

    builder.position_at_end(rehash_skip_bb)
    old_i_next = builder.add(old_i_val, one_i32, name="old_i_next")
    builder.store(old_i_next, old_i)
    builder.branch(rehash_loop_cond_bb)

    builder.position_at_end(rehash_loop_end_bb)

    builder.store(new_bucket_ptr, buckets_data_ptr)

    builder.store(new_capacity, capacity_ptr)

    builder.store(zero_i32, tombstones_ptr)

    # Free old buckets to prevent memory leak
    old_buckets_void_ptr = builder.bitcast(old_buckets_data, ir.PointerType(codegen.types.i8), name="old_buckets_void_ptr")
    free_func = codegen.get_free_func()
    builder.call(free_func, [old_buckets_void_ptr])


def emit_hashmap_rehash(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.rehash() -> ~"""
    builder = codegen.builder

    capacity_ptr = builder.gep(hashmap_value, HASHMAP_CAPACITY_INDICES, name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    emit_hashmap_resize_to_capacity(codegen, hashmap_value, hashmap_type, capacity)

    return ir.Constant(codegen.types.i32, 0)


def emit_hashmap_free(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.free() -> ~ -- the map stays usable, empty, at the initial capacity."""
    builder = codegen.builder

    zero_i32 = ir.Constant(codegen.types.i32, 0)
    initial_capacity = ir.Constant(codegen.types.i32, 16)

    fields, entry_type = _release_buckets(codegen, hashmap_value, hashmap_type, null_guard=False)

    entry_size = get_element_size_constant(codegen, entry_type)
    total_bytes = builder.mul(entry_size, initial_capacity, name="bucket_bytes")

    total_bytes_i64 = builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    new_bucket_ptr_i8 = emit_malloc(codegen, builder, total_bytes_i64)
    new_bucket_ptr = builder.bitcast(new_bucket_ptr_i8, ir.PointerType(entry_type), name="new_buckets_ptr")

    emit_init_buckets_empty(codegen, new_bucket_ptr, initial_capacity)

    builder.store(zero_i32, fields.size)
    builder.store(initial_capacity, fields.capacity)
    builder.store(zero_i32, fields.tombstones)
    builder.store(new_bucket_ptr, fields.buckets_data)

    return ir.Constant(codegen.types.i32, 0)


def emit_hashmap_destroy(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.destroy() -> ~ -- the map keeps no buckets and capacity 0."""
    builder = codegen.builder

    zero_i32 = ir.Constant(codegen.types.i32, 0)

    fields, entry_type = _release_buckets(codegen, hashmap_value, hashmap_type, null_guard=True)

    builder.store(zero_i32, fields.size)
    builder.store(zero_i32, fields.capacity)
    builder.store(zero_i32, fields.tombstones)
    builder.store(ir.Constant(ir.PointerType(entry_type), None), fields.buckets_data)

    return ir.Constant(codegen.types.i32, 0)


def _release_buckets(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType,
    *,
    null_guard: bool,
) -> tuple[HashMapFields, ir.Type]:
    """Destroy every entry of a map and free its buckets.

    A destroyed map has null buckets, so a caller that can meet one sets `null_guard`.
    """
    builder = codegen.builder

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    entry_type = get_entry_type(codegen, key_type, value_type)

    fields = get_hashmap_field_ptrs(codegen, hashmap_value)
    old_capacity = builder.load(fields.capacity, name="old_capacity")
    old_buckets_data = builder.load(fields.buckets_data, name="old_buckets_data")

    emit_destroy_all_entries(codegen, old_buckets_data, old_capacity, key_type,
                             value_type, null_guard=null_guard)

    def free_buckets() -> None:
        old_buckets_void_ptr = builder.bitcast(old_buckets_data, ir.PointerType(codegen.types.i8), name="old_buckets_void_ptr")
        builder.call(codegen.get_free_func(), [old_buckets_void_ptr])

    if not null_guard:
        free_buckets()
        return fields, entry_type

    is_not_null = builder.icmp_unsigned("!=", old_buckets_data, ir.Constant(ir.PointerType(entry_type), None))
    with builder.if_then(is_not_null):
        free_buckets()
    return fields, entry_type
