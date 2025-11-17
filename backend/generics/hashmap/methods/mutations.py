"""
HashMap<K, V> mutation method implementations.

This module contains HashMap methods that modify the map:
- insert (add/update key-value pairs with auto-resize)
- remove (delete entries, mark tombstones)
- rehash (rebuild without tombstones)
- free (destroy all entries, reset to initial capacity)
- destroy (destroy all entries, set to unusable state)
"""

from typing import Any, Optional
from semantics.ast import MethodCall, Name
from semantics.typesys import StructType, BuiltinType
import llvmlite.ir as ir
from ..types import get_entry_type, extract_key_value_types, ENTRY_EMPTY, ENTRY_OCCUPIED, ENTRY_TOMBSTONE
from ..utils import emit_key_equality_check, emit_insert_entry
from internals.errors import raise_internal_error


def emit_hashmap_insert(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.insert(K key, V value) -> ~

    Inserts or updates a key-value pair in the HashMap.
    Automatically resizes when load factor exceeds 0.75.

    Algorithm:
    1. Check load factor: (size + tombstones) / capacity > 0.75
    2. If exceeded, resize to next power-of-two capacity (doubles current)
    3. Hash the key to get hash value
    4. Linear probe to find slot:
       - If Occupied with same key: update value
       - If Empty: insert new entry, size++
       - If Tombstone: reuse slot, insert entry, size++, tombstones--
    5. Return unit (~)

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with arguments.
        hashmap_value: The HashMap struct value (pass by value, not pointer).
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Unit value (~).
    """
    from stdlib.src.common import get_builtin_method
    import backend.types.primitives.hashing  # noqa: F401 - Ensure hash methods are registered

    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)

    # Constants
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)
    zero_i8 = ir.Constant(codegen.types.i8, 0)

    # Emit the key and value arguments
    if len(expr.args) != 2:
        raise_internal_error("CE0023", method="insert", expected=2, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])
    value_value = codegen.expressions.emit_expr(expr.args[1])

    # Get pointers to HashMap fields
    # hashmap_value should be a POINTER to the HashMap struct (like array_value for arrays)
    # HashMap struct: {buckets, size, capacity, tombstones}
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 3)], name="tombstones_ptr")

    # Load current values
    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    # Get buckets array pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Check load factor and resize if needed
    # Load factor = (size + tombstones) / capacity
    # If load factor > 0.75, resize to next power-of-two capacity
    # We use integer arithmetic: (size + tombstones) * 4 > capacity * 3
    size_plus_tombstones = builder.add(size, tombstones, name="size_plus_tombstones")
    lhs = builder.mul(size_plus_tombstones, ir.Constant(codegen.types.i32, 4), name="lhs")
    rhs = builder.mul(capacity, ir.Constant(codegen.types.i32, 3), name="rhs")
    should_resize = builder.icmp_unsigned(">", lhs, rhs, name="should_resize")

    # Create conditional resize
    resize_bb = builder.append_basic_block(name="resize_hashmap")
    continue_insert_bb = builder.append_basic_block(name="continue_insert")
    builder.cbranch(should_resize, resize_bb, continue_insert_bb)

    # Resize block
    builder.position_at_end(resize_bb)
    # Next capacity = capacity * 2 (simple doubling, guaranteed power-of-two)
    new_capacity = builder.shl(capacity, ir.Constant(codegen.types.i32, 1), name="new_capacity")

    # Call resize helper
    from .mutations import emit_hashmap_resize_to_capacity
    emit_hashmap_resize_to_capacity(codegen, hashmap_value, hashmap_type, new_capacity)
    builder.branch(continue_insert_bb)

    # Continue with insertion - reload capacity and buckets_data after potential resize
    builder.position_at_end(continue_insert_bb)
    capacity = builder.load(capacity_ptr, name="capacity_current")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data_current")

    # Hash the key (register on-demand if needed for array types)
    from backend.generics.hashmap.types import get_key_hash_method
    hash_method = get_key_hash_method(key_type)
    if hash_method is None:
        raise_internal_error("CE0053", type=key_type)

    # Create a fake MethodCall for the hash emitter
    fake_call = MethodCall(
        receiver=Name(id="key", loc=(0, 0)),
        method="hash",
        args=[],
        loc=(0, 0)
    )

    # Emit hash call - key_value is already a value (not pointer)
    hash_value = hash_method.llvm_emitter(codegen, fake_call, key_value, key_llvm, False)

    # Truncate hash to i32 for indexing
    hash_i32 = builder.trunc(hash_value, codegen.types.i32, name="hash_i32")

    # Linear probing loop
    # Variables for probing
    probe_offset = builder.alloca(codegen.types.i32, name="probe_offset")
    builder.store(zero_i32, probe_offset)

    first_tombstone_idx = builder.alloca(codegen.types.i32, name="first_tombstone_idx")
    builder.store(ir.Constant(codegen.types.i32, -1), first_tombstone_idx)  # -1 means no tombstone found

    probe_loop_bb = builder.append_basic_block(name="probe_loop")
    probe_body_bb = builder.append_basic_block(name="probe_body")
    probe_occupied_bb = builder.append_basic_block(name="probe_occupied")
    probe_empty_bb = builder.append_basic_block(name="probe_empty")
    probe_tombstone_bb = builder.append_basic_block(name="probe_tombstone")
    probe_continue_bb = builder.append_basic_block(name="probe_continue")

    builder.branch(probe_loop_bb)

    # Probe loop
    builder.position_at_end(probe_loop_bb)
    probe_offset_val = builder.load(probe_offset, name="probe_offset_val")

    # Calculate index: (hash + probe_offset) & (capacity - 1)
    # Fast bitwise AND works because capacity is power-of-two
    hash_plus_offset = builder.add(hash_i32, probe_offset_val, name="hash_plus_offset")
    capacity_minus_1 = builder.sub(capacity, ir.Constant(codegen.types.i32, 1), name="capacity_minus_1")
    index = builder.and_(hash_plus_offset, capacity_minus_1, name="index")

    # Get entry pointer
    entry_ptr = builder.gep(buckets_data, [index], name="entry_ptr")

    # Load entry state
    state_ptr = builder.gep(entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="state_ptr")
    state = builder.load(state_ptr, name="state")

    # Branch based on state
    builder.branch(probe_body_bb)

    builder.position_at_end(probe_body_bb)
    is_empty = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_EMPTY), name="is_empty")
    is_occupied = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    is_tombstone = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_TOMBSTONE), name="is_tombstone")

    # Create branch for state check
    insert_done_bb = builder.append_basic_block(name="insert_done")

    # Check if occupied
    check_tombstone_bb = builder.append_basic_block(name="check_tombstone")
    builder.cbranch(is_occupied, probe_occupied_bb, check_tombstone_bb)

    # Occupied case: check if keys match
    builder.position_at_end(probe_occupied_bb)
    entry_key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="entry_key_ptr")
    entry_key = builder.load(entry_key_ptr, name="entry_key")

    # Compare keys using ==
    keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)

    update_value_bb = builder.append_basic_block(name="update_value")
    builder.cbranch(keys_equal, update_value_bb, probe_continue_bb)

    # Update existing value
    builder.position_at_end(update_value_bb)
    entry_value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="entry_value_ptr")
    builder.store(value_value, entry_value_ptr)
    builder.branch(insert_done_bb)

    # Check if tombstone
    builder.position_at_end(check_tombstone_bb)
    check_empty_bb = builder.append_basic_block(name="check_empty")
    builder.cbranch(is_tombstone, probe_tombstone_bb, check_empty_bb)

    # Tombstone case: remember first tombstone, keep probing
    builder.position_at_end(probe_tombstone_bb)
    first_tombstone = builder.load(first_tombstone_idx, name="first_tombstone")
    is_first = builder.icmp_signed("==", first_tombstone, ir.Constant(codegen.types.i32, -1), name="is_first_tombstone")

    record_tombstone_bb = builder.append_basic_block(name="record_tombstone")
    builder.cbranch(is_first, record_tombstone_bb, probe_continue_bb)

    builder.position_at_end(record_tombstone_bb)
    builder.store(index, first_tombstone_idx)
    builder.branch(probe_continue_bb)

    # Empty case: insert here (or at first tombstone if we found one)
    builder.position_at_end(check_empty_bb)
    builder.cbranch(is_empty, probe_empty_bb, probe_continue_bb)

    builder.position_at_end(probe_empty_bb)

    # Check if we have a tombstone to reuse
    first_tombstone = builder.load(first_tombstone_idx, name="first_tombstone_final")
    has_tombstone = builder.icmp_signed("!=", first_tombstone, ir.Constant(codegen.types.i32, -1), name="has_tombstone")

    use_tombstone_bb = builder.append_basic_block(name="use_tombstone")
    use_empty_bb = builder.append_basic_block(name="use_empty")

    builder.cbranch(has_tombstone, use_tombstone_bb, use_empty_bb)

    # Insert at tombstone location
    builder.position_at_end(use_tombstone_bb)
    tombstone_entry_ptr = builder.gep(buckets_data, [first_tombstone], name="tombstone_entry_ptr")
    emit_insert_entry(codegen, tombstone_entry_ptr, key_value, value_value, entry_type)

    # Update size++, tombstones--
    new_size = builder.add(size, one_i32, name="new_size")
    builder.store(new_size, size_ptr)
    new_tombstones = builder.sub(tombstones, one_i32, name="new_tombstones")
    builder.store(new_tombstones, tombstones_ptr)
    builder.branch(insert_done_bb)

    # Insert at empty location
    builder.position_at_end(use_empty_bb)
    emit_insert_entry(codegen, entry_ptr, key_value, value_value, entry_type)

    # Update size++
    new_size = builder.add(size, one_i32, name="new_size")
    builder.store(new_size, size_ptr)
    builder.branch(insert_done_bb)

    # Continue probing
    builder.position_at_end(probe_continue_bb)
    probe_offset_next = builder.add(probe_offset_val, one_i32, name="probe_offset_next")
    builder.store(probe_offset_next, probe_offset)
    builder.branch(probe_loop_bb)

    # Done
    builder.position_at_end(insert_done_bb)

    # Return unit (~) - represented as i32 constant 0
    return ir.Constant(codegen.types.i32, 0)



def emit_hashmap_remove(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.remove(K key) -> Maybe<V>

    Removes a key-value pair from the HashMap.

    Algorithm:
    1. Hash the key to get hash value
    2. Linear probe to find slot:
       - If Occupied with matching key:
         * Save the value
         * Mark entry as Tombstone
         * Decrement size, increment tombstones
         * Return Maybe.Some(saved_value)
       - If Empty: return Maybe.None() (not found)
       - If Tombstone or occupied with different key: continue probing
    3. Return Maybe<V>

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with key argument.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Maybe<V> enum value.
    """
    from stdlib.src.common import get_builtin_method
    from semantics.ast import MethodCall, Name
    import backend.types.primitives.hashing  # noqa: F401

    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)
    value_llvm = codegen.types.ll_type(value_type)

    # Constants
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)

    # Emit the key argument
    if len(expr.args) != 1:
        raise_internal_error("CE0023", method="remove", expected=1, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])

    # Get HashMap field pointers
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 3)], name="tombstones_ptr")

    # Load values
    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    # Get buckets array pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Hash the key (register on-demand if needed for array types)
    from backend.generics.hashmap.types import get_key_hash_method
    hash_method = get_key_hash_method(key_type)
    if hash_method is None:
        raise_internal_error("CE0053", type=key_type)

    fake_call = MethodCall(
        receiver=Name(id="key", loc=(0, 0)),
        method="hash",
        args=[],
        loc=(0, 0)
    )

    hash_value = hash_method.llvm_emitter(codegen, fake_call, key_value, codegen.types.ll_type(key_type), False)
    hash_i32 = builder.trunc(hash_value, codegen.types.i32, name="hash_i32")

    # Linear probing loop
    probe_offset = builder.alloca(codegen.types.i32, name="probe_offset")
    builder.store(zero_i32, probe_offset)

    probe_loop_bb = builder.append_basic_block(name="remove_probe_loop")
    probe_body_bb = builder.append_basic_block(name="remove_probe_body")
    probe_occupied_bb = builder.append_basic_block(name="remove_probe_occupied")
    probe_empty_bb = builder.append_basic_block(name="remove_probe_empty")
    probe_continue_bb = builder.append_basic_block(name="remove_probe_continue")
    found_bb = builder.append_basic_block(name="remove_found")
    not_found_bb = builder.append_basic_block(name="remove_not_found")
    remove_done_bb = builder.append_basic_block(name="remove_done")

    builder.branch(probe_loop_bb)

    # Probe loop
    builder.position_at_end(probe_loop_bb)
    probe_offset_val = builder.load(probe_offset, name="probe_offset_val")

    # Calculate index: (hash + probe_offset) & (capacity - 1)
    # Fast bitwise AND works because capacity is power-of-two
    hash_plus_offset = builder.add(hash_i32, probe_offset_val, name="hash_plus_offset")
    capacity_minus_1 = builder.sub(capacity, one_i32, name="capacity_minus_1")
    index = builder.and_(hash_plus_offset, capacity_minus_1, name="index")

    # Get entry pointer
    entry_ptr = builder.gep(buckets_data, [index], name="entry_ptr")

    # Load entry state
    state_ptr = builder.gep(entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="state_ptr")
    state = builder.load(state_ptr, name="state")

    # Branch based on state
    builder.branch(probe_body_bb)

    builder.position_at_end(probe_body_bb)
    is_empty = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_EMPTY), name="is_empty")
    is_occupied = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")

    # If empty, not found
    builder.cbranch(is_empty, probe_empty_bb, builder.append_basic_block(name="check_occupied"))

    # Check if occupied
    builder.position_at_end(builder.function.basic_blocks[-1])
    builder.cbranch(is_occupied, probe_occupied_bb, probe_continue_bb)

    # Empty case: not found
    builder.position_at_end(probe_empty_bb)
    builder.branch(not_found_bb)

    # Occupied case: check if keys match
    builder.position_at_end(probe_occupied_bb)
    entry_key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="entry_key_ptr")
    entry_key = builder.load(entry_key_ptr, name="entry_key")

    # Compare keys
    keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)
    builder.cbranch(keys_equal, found_bb, probe_continue_bb)

    # Found: remove entry and return Maybe.Some(value)
    builder.position_at_end(found_bb)

    # Save the value before marking as tombstone
    entry_value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="entry_value_ptr")
    entry_value = builder.load(entry_value_ptr, name="entry_value")

    # Mark entry as Tombstone
    builder.store(ir.Constant(codegen.types.i8, ENTRY_TOMBSTONE), state_ptr)

    # Update size-- and tombstones++
    new_size = builder.sub(size, one_i32, name="new_size")
    builder.store(new_size, size_ptr)
    new_tombstones = builder.add(tombstones, one_i32, name="new_tombstones")
    builder.store(new_tombstones, tombstones_ptr)

    # Get Maybe<V> enum type
    # Format the type name properly
    from semantics.typesys import BuiltinType
    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    maybe_enum_name = f"Maybe<{type_str}>"
    maybe_enum_type = codegen.enum_table.by_name.get(maybe_enum_name)

    if maybe_enum_type is None:
        # Maybe<V> not monomorphized - create it on the fly
        # This happens when HashMap.remove() is used but Maybe<V> wasn't used elsewhere
        from backend.generics.maybe import ensure_maybe_type_exists
        maybe_enum_type = ensure_maybe_type_exists(codegen, value_type)
        if maybe_enum_type is None:
            # Still couldn't create it - this shouldn't happen
            available = list(codegen.enum_table.by_name.keys())
            raise_internal_error("CE0047", type=type_str)

    # Get LLVM type for Maybe<V> enum: {i32 tag, [N x i8] data}
    maybe_llvm_type = codegen.types.get_enum_type(maybe_enum_type)

    # Create Maybe.Some(value)
    maybe_some = ir.Constant(maybe_llvm_type, ir.Undefined)
    some_tag = ir.Constant(codegen.types.i32, 0)  # Some is first variant
    maybe_some = builder.insert_value(maybe_some, some_tag, 0, name="maybe_some_tag")

    # Pack the value into the data field [N x i8]
    data_array_type = maybe_llvm_type.elements[1]  # [N x i8]
    data_ptr = builder.alloca(data_array_type, name="some_data_alloc")
    value_ptr = builder.bitcast(data_ptr, ir.PointerType(value_llvm), name="value_ptr")
    builder.store(entry_value, value_ptr)
    data_value = builder.load(data_ptr, name="some_data")
    maybe_some = builder.insert_value(maybe_some, data_value, 1, name="maybe_some_value")

    builder.branch(remove_done_bb)

    # Not found: return Maybe.None()
    builder.position_at_end(not_found_bb)
    maybe_none = ir.Constant(maybe_llvm_type, ir.Undefined)
    none_tag = ir.Constant(codegen.types.i32, 1)  # None is second variant
    maybe_none = builder.insert_value(maybe_none, none_tag, 0, name="maybe_none_tag")
    # Data field is undefined for None
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    maybe_none = builder.insert_value(maybe_none, undef_data, 1, name="maybe_none_data")
    builder.branch(remove_done_bb)

    # Continue probing (tombstone or different key)
    builder.position_at_end(probe_continue_bb)
    probe_offset_next = builder.add(probe_offset_val, one_i32, name="probe_offset_next")
    builder.store(probe_offset_next, probe_offset)
    builder.branch(probe_loop_bb)

    # Done: merge results
    builder.position_at_end(remove_done_bb)
    result_phi = builder.phi(maybe_llvm_type, name="remove_result")
    result_phi.add_incoming(maybe_some, found_bb)
    result_phi.add_incoming(maybe_none, not_found_bb)

    return result_phi




def emit_hashmap_resize_to_capacity(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType,
    new_capacity: ir.Value
) -> None:
    """Internal helper: resize HashMap to a specific capacity.

    Rebuilds the HashMap with a new capacity, rehashing all occupied entries.
    This is used by both rehash() (same capacity) and automatic resize (larger capacity).

    Note: This function handles on-demand hash registration for array key types,
    since array types used as HashMap keys may not be registered in Pass 1.8 if they
    only appear as HashMap type parameters (not in struct/enum fields).

    Algorithm:
    1. Allocate new buckets array with new_capacity
    2. Initialize all entries to Empty
    3. Iterate through old buckets
    4. For each Occupied entry, rehash and insert into new buckets
    5. Replace old buckets with new buckets
    6. Update capacity field
    7. Reset tombstones to 0
    8. Free old buckets

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.
        new_capacity: The new capacity (must be power-of-two).
    """
    from stdlib.src.common import get_builtin_method
    from semantics.ast import MethodCall, Name
    import backend.types.primitives.hashing  # noqa: F401

    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Constants
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)

    # Get HashMap field pointers
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 3)], name="tombstones_ptr")

    # Load current values
    size = builder.load(size_ptr, name="size")
    old_capacity = builder.load(capacity_ptr, name="old_capacity")

    # Get old buckets pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    old_buckets_data = builder.load(buckets_data_ptr, name="old_buckets_data")

    # Allocate new buckets array with new_capacity
    null_ptr = ir.Constant(ir.PointerType(entry_type), None)
    one = ir.Constant(codegen.types.i32, 1)
    gep = builder.gep(null_ptr, [one], name="sizeof_gep")
    entry_size = builder.ptrtoint(gep, codegen.types.i32, name="entry_size")
    total_bytes = builder.mul(entry_size, new_capacity, name="bucket_bytes")

    # Call malloc
    malloc_fn = codegen.get_malloc_func()
    total_bytes_i64 = builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    new_bucket_ptr_i8 = builder.call(malloc_fn, [total_bytes_i64], name="new_buckets_raw")
    new_bucket_ptr = builder.bitcast(new_bucket_ptr_i8, ir.PointerType(entry_type), name="new_buckets_ptr")

    # Initialize all new entries to Empty
    init_i = builder.alloca(codegen.types.i32, name="init_i")
    builder.store(zero_i32, init_i)

    init_loop_cond_bb = builder.append_basic_block(name="init_loop_cond")
    init_loop_body_bb = builder.append_basic_block(name="init_loop_body")
    init_loop_end_bb = builder.append_basic_block(name="init_loop_end")

    builder.branch(init_loop_cond_bb)

    # Init loop condition: i < new_capacity
    builder.position_at_end(init_loop_cond_bb)
    init_i_val = builder.load(init_i, name="init_i_val")
    init_cond = builder.icmp_unsigned("<", init_i_val, new_capacity, name="init_cond")
    builder.cbranch(init_cond, init_loop_body_bb, init_loop_end_bb)

    # Init loop body: set entry[i].state = ENTRY_EMPTY
    builder.position_at_end(init_loop_body_bb)
    init_i_val = builder.load(init_i, name="init_i_val")
    new_entry_ptr = builder.gep(new_bucket_ptr, [init_i_val], name="new_entry_ptr")
    new_state_ptr = builder.gep(new_entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="new_state_ptr")
    builder.store(ir.Constant(codegen.types.i8, ENTRY_EMPTY), new_state_ptr)

    init_i_next = builder.add(init_i_val, one_i32, name="init_i_next")
    builder.store(init_i_next, init_i)
    builder.branch(init_loop_cond_bb)

    # After init loop: iterate through old buckets and reinsert Occupied entries
    builder.position_at_end(init_loop_end_bb)

    # Get hash method for keys (register on-demand if not already registered)
    from backend.generics.hashmap.types import get_key_hash_method
    hash_method = get_key_hash_method(key_type)
    if hash_method is None:
        raise_internal_error("CE0053", type=key_type)

    # Iterate through old buckets
    old_i = builder.alloca(codegen.types.i32, name="old_i")
    builder.store(zero_i32, old_i)

    rehash_loop_cond_bb = builder.append_basic_block(name="rehash_loop_cond")
    rehash_loop_body_bb = builder.append_basic_block(name="rehash_loop_body")
    rehash_check_occupied_bb = builder.append_basic_block(name="rehash_check_occupied")
    rehash_reinsert_bb = builder.append_basic_block(name="rehash_reinsert")
    rehash_skip_bb = builder.append_basic_block(name="rehash_skip")
    rehash_loop_end_bb = builder.append_basic_block(name="rehash_loop_end")

    builder.branch(rehash_loop_cond_bb)

    # Rehash loop condition: i < old_capacity
    builder.position_at_end(rehash_loop_cond_bb)
    old_i_val = builder.load(old_i, name="old_i_val")
    rehash_cond = builder.icmp_unsigned("<", old_i_val, old_capacity, name="rehash_cond")
    builder.cbranch(rehash_cond, rehash_loop_body_bb, rehash_loop_end_bb)

    # Rehash loop body: check if entry is Occupied
    builder.position_at_end(rehash_loop_body_bb)
    old_i_val = builder.load(old_i, name="old_i_val")
    old_entry_ptr = builder.gep(old_buckets_data, [old_i_val], name="old_entry_ptr")
    old_state_ptr = builder.gep(old_entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="old_state_ptr")
    old_state = builder.load(old_state_ptr, name="old_state")

    builder.branch(rehash_check_occupied_bb)

    builder.position_at_end(rehash_check_occupied_bb)
    is_occupied = builder.icmp_unsigned("==", old_state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    builder.cbranch(is_occupied, rehash_reinsert_bb, rehash_skip_bb)

    # Reinsert occupied entry into new buckets
    builder.position_at_end(rehash_reinsert_bb)

    # Load key and value from old entry
    old_key_ptr = builder.gep(old_entry_ptr, [zero_i32, zero_i32], name="old_key_ptr")
    old_key = builder.load(old_key_ptr, name="old_key")
    old_value_ptr = builder.gep(old_entry_ptr, [zero_i32, one_i32], name="old_value_ptr")
    old_value = builder.load(old_value_ptr, name="old_value")

    # Hash the key
    fake_call = MethodCall(
        receiver=Name(id="key", loc=(0, 0)),
        method="hash",
        args=[],
        loc=(0, 0)
    )
    hash_value = hash_method.llvm_emitter(codegen, fake_call, old_key, codegen.types.ll_type(key_type), False)
    hash_i32 = builder.trunc(hash_value, codegen.types.i32, name="hash_i32")

    # Linear probe to find empty slot in new buckets
    probe_offset = builder.alloca(codegen.types.i32, name="probe_offset")
    builder.store(zero_i32, probe_offset)

    probe_loop_bb = builder.append_basic_block(name="rehash_probe_loop")
    probe_check_bb = builder.append_basic_block(name="rehash_probe_check")
    probe_found_empty_bb = builder.append_basic_block(name="rehash_probe_found_empty")
    probe_continue_bb = builder.append_basic_block(name="rehash_probe_continue")

    builder.branch(probe_loop_bb)

    # Probe loop for new buckets
    builder.position_at_end(probe_loop_bb)
    probe_offset_val = builder.load(probe_offset, name="probe_offset_val")
    hash_plus_offset = builder.add(hash_i32, probe_offset_val, name="hash_plus_offset")
    # Calculate index: (hash + probe_offset) & (new_capacity - 1)
    # Fast bitwise AND works because capacity is power-of-two
    new_capacity_minus_1 = builder.sub(new_capacity, one_i32, name="new_capacity_minus_1")
    new_index = builder.and_(hash_plus_offset, new_capacity_minus_1, name="new_index")

    new_probe_entry_ptr = builder.gep(new_bucket_ptr, [new_index], name="new_probe_entry_ptr")
    new_probe_state_ptr = builder.gep(new_probe_entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="new_probe_state_ptr")
    new_probe_state = builder.load(new_probe_state_ptr, name="new_probe_state")

    builder.branch(probe_check_bb)

    builder.position_at_end(probe_check_bb)
    is_empty = builder.icmp_unsigned("==", new_probe_state, ir.Constant(codegen.types.i8, ENTRY_EMPTY), name="is_empty")
    builder.cbranch(is_empty, probe_found_empty_bb, probe_continue_bb)

    # Found empty slot: insert entry
    builder.position_at_end(probe_found_empty_bb)
    emit_insert_entry(codegen, new_probe_entry_ptr, old_key, old_value, entry_type)
    builder.branch(rehash_skip_bb)

    # Continue probing
    builder.position_at_end(probe_continue_bb)
    probe_offset_next = builder.add(probe_offset_val, one_i32, name="probe_offset_next")
    builder.store(probe_offset_next, probe_offset)
    builder.branch(probe_loop_bb)

    # Skip non-occupied entries
    builder.position_at_end(rehash_skip_bb)
    old_i_next = builder.add(old_i_val, one_i32, name="old_i_next")
    builder.store(old_i_next, old_i)
    builder.branch(rehash_loop_cond_bb)

    # After rehashing all entries: update HashMap
    builder.position_at_end(rehash_loop_end_bb)

    # Store new buckets pointer
    builder.store(new_bucket_ptr, buckets_data_ptr)

    # Update capacity to new_capacity
    builder.store(new_capacity, capacity_ptr)

    # Reset tombstones to 0
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
    """Emit HashMap<K, V>.rehash() -> ~

    Rebuilds the HashMap without tombstones, using the same capacity.

    This delegates to the internal resize helper with the current capacity.

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Unit value (~).
    """
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.types.i32, 0)

    # Load current capacity
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    # Resize to same capacity (removes tombstones)
    emit_hashmap_resize_to_capacity(codegen, hashmap_value, hashmap_type, capacity)

    # Return unit (~)
    return ir.Constant(codegen.types.i32, 0)




def emit_hashmap_free(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.free() -> ~

    Deallocates all keys and values recursively, frees the buckets array,
    and resets the HashMap to its initial empty state (capacity 16).

    This method prevents memory leaks by:
    1. Iterating through all buckets
    2. For each occupied entry, recursively destroying key and value
    3. Freeing the buckets array
    4. Allocating new empty buckets with initial capacity (16)
    5. Resetting size, capacity, and tombstones

    Algorithm:
    1. Load old buckets and capacity
    2. Iterate through all buckets [0..capacity-1]
    3. For each bucket:
       - Check if state == ENTRY_OCCUPIED
       - If occupied, recursively destroy key and value using emit_value_destructor
    4. Free old buckets array
    5. Allocate new buckets array (capacity 16)
    6. Initialize all entries to Empty
    7. Update HashMap fields: size=0, capacity=16, tombstones=0
    8. Return unit (~)

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Unit value (~).
    """
    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)
    key_llvm = codegen.types.ll_type(key_type)
    value_llvm = codegen.types.ll_type(value_type)

    # Constants
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)
    initial_capacity = ir.Constant(codegen.types.i32, 16)

    # Get HashMap field pointers
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 3)], name="tombstones_ptr")

    # Load old values
    old_capacity = builder.load(capacity_ptr, name="old_capacity")

    # Get old buckets pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    old_buckets_data = builder.load(buckets_data_ptr, name="old_buckets_data")

    # Iterate through old buckets and destroy occupied entries
    loop_i = builder.alloca(codegen.types.i32, name="loop_i")
    builder.store(zero_i32, loop_i)

    destroy_loop_cond_bb = builder.append_basic_block(name="destroy_loop_cond")
    destroy_loop_body_bb = builder.append_basic_block(name="destroy_loop_body")
    destroy_check_occupied_bb = builder.append_basic_block(name="destroy_check_occupied")
    destroy_entry_bb = builder.append_basic_block(name="destroy_entry")
    destroy_skip_bb = builder.append_basic_block(name="destroy_skip")
    destroy_loop_end_bb = builder.append_basic_block(name="destroy_loop_end")

    builder.branch(destroy_loop_cond_bb)

    # Loop condition: i < old_capacity
    builder.position_at_end(destroy_loop_cond_bb)
    i_val = builder.load(loop_i, name="i_val")
    loop_cond = builder.icmp_unsigned("<", i_val, old_capacity, name="loop_cond")
    builder.cbranch(loop_cond, destroy_loop_body_bb, destroy_loop_end_bb)

    # Loop body: check if entry is occupied
    builder.position_at_end(destroy_loop_body_bb)
    i_val = builder.load(loop_i, name="i_val")
    entry_ptr = builder.gep(old_buckets_data, [i_val], name="entry_ptr")
    state_ptr = builder.gep(entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="state_ptr")
    state = builder.load(state_ptr, name="state")

    builder.branch(destroy_check_occupied_bb)

    builder.position_at_end(destroy_check_occupied_bb)
    is_occupied = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
    builder.cbranch(is_occupied, destroy_entry_bb, destroy_skip_bb)

    # Destroy occupied entry: recursively destroy key and value
    builder.position_at_end(destroy_entry_bb)

    # Get pointers to key and value
    key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="key_ptr")
    value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="value_ptr")

    # Recursively destroy key and value using the general destructor
    from backend.destructors import emit_value_destructor
    emit_value_destructor(codegen, builder, key_ptr, key_type)
    emit_value_destructor(codegen, builder, value_ptr, value_type)

    builder.branch(destroy_skip_bb)

    # Skip non-occupied entries
    builder.position_at_end(destroy_skip_bb)
    i_next = builder.add(i_val, one_i32, name="i_next")
    builder.store(i_next, loop_i)
    builder.branch(destroy_loop_cond_bb)

    # After destroying all entries, free old buckets
    builder.position_at_end(destroy_loop_end_bb)

    # Free old buckets array
    old_buckets_void_ptr = builder.bitcast(old_buckets_data, ir.PointerType(codegen.types.i8), name="old_buckets_void_ptr")
    free_func = codegen.get_free_func()
    builder.call(free_func, [old_buckets_void_ptr])

    # Allocate new buckets with initial capacity (16)
    null_ptr = ir.Constant(ir.PointerType(entry_type), None)
    one = ir.Constant(codegen.types.i32, 1)
    gep = builder.gep(null_ptr, [one], name="sizeof_gep")
    entry_size = builder.ptrtoint(gep, codegen.types.i32, name="entry_size")
    total_bytes = builder.mul(entry_size, initial_capacity, name="bucket_bytes")

    # Call malloc
    malloc_fn = codegen.get_malloc_func()
    total_bytes_i64 = builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    new_bucket_ptr_i8 = builder.call(malloc_fn, [total_bytes_i64], name="new_buckets_raw")
    new_bucket_ptr = builder.bitcast(new_bucket_ptr_i8, ir.PointerType(entry_type), name="new_buckets_ptr")

    # Initialize all new entries to Empty
    init_i = builder.alloca(codegen.types.i32, name="init_i")
    builder.store(zero_i32, init_i)

    init_loop_cond_bb = builder.append_basic_block(name="init_loop_cond")
    init_loop_body_bb = builder.append_basic_block(name="init_loop_body")
    init_loop_end_bb = builder.append_basic_block(name="init_loop_end")

    builder.branch(init_loop_cond_bb)

    # Init loop condition: i < initial_capacity (16)
    builder.position_at_end(init_loop_cond_bb)
    init_i_val = builder.load(init_i, name="init_i_val")
    init_cond = builder.icmp_unsigned("<", init_i_val, initial_capacity, name="init_cond")
    builder.cbranch(init_cond, init_loop_body_bb, init_loop_end_bb)

    # Init loop body: set entry[i].state = ENTRY_EMPTY
    builder.position_at_end(init_loop_body_bb)
    init_i_val = builder.load(init_i, name="init_i_val")
    new_entry_ptr = builder.gep(new_bucket_ptr, [init_i_val], name="new_entry_ptr")
    new_state_ptr = builder.gep(new_entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="new_state_ptr")
    builder.store(ir.Constant(codegen.types.i8, ENTRY_EMPTY), new_state_ptr)

    init_i_next = builder.add(init_i_val, one_i32, name="init_i_next")
    builder.store(init_i_next, init_i)
    builder.branch(init_loop_cond_bb)

    # After initialization: update HashMap fields
    builder.position_at_end(init_loop_end_bb)

    # Store new values: size=0, capacity=16, tombstones=0
    builder.store(zero_i32, size_ptr)
    builder.store(initial_capacity, capacity_ptr)
    builder.store(zero_i32, tombstones_ptr)
    builder.store(new_bucket_ptr, buckets_data_ptr)

    # Return unit (~)
    return ir.Constant(codegen.types.i32, 0)



def emit_hashmap_destroy(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.destroy() -> ~

    Deallocates all keys and values recursively, frees the buckets array,
    and sets buckets to null (makes HashMap unusable).

    This method prevents memory leaks by:
    1. Iterating through all buckets
    2. For each occupied entry, recursively destroying key and value
    3. Freeing the buckets array
    4. Setting all fields to 0/null (HashMap is unusable after this)

    Algorithm:
    1. Load old buckets and capacity
    2. Iterate through all buckets [0..capacity-1]
    3. For each bucket:
       - Check if state == ENTRY_OCCUPIED
       - If occupied, recursively destroy key and value using emit_value_destructor
    4. Free old buckets array
    5. Reset HashMap fields: size=0, capacity=0, tombstones=0, buckets=null
    6. Return unit (~)

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Unit value (~).
    """
    builder = codegen.builder

    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)

    # Constants
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)

    # Get HashMap field pointers
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 3)], name="tombstones_ptr")

    # Load old values
    old_capacity = builder.load(capacity_ptr, name="old_capacity")

    # Get old buckets pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    old_buckets_data = builder.load(buckets_data_ptr, name="old_buckets_data")

    # Check if buckets pointer is not null (avoid double-free)
    null_entry_ptr = ir.Constant(ir.PointerType(entry_type), None)
    is_not_null = builder.icmp_unsigned("!=", old_buckets_data, null_entry_ptr)

    with builder.if_then(is_not_null):
        # Iterate through old buckets and destroy occupied entries
        loop_i = builder.alloca(codegen.types.i32, name="loop_i")
        builder.store(zero_i32, loop_i)

        destroy_loop_cond_bb = builder.append_basic_block(name="destroy_loop_cond")
        destroy_loop_body_bb = builder.append_basic_block(name="destroy_loop_body")
        destroy_check_occupied_bb = builder.append_basic_block(name="destroy_check_occupied")
        destroy_entry_bb = builder.append_basic_block(name="destroy_entry")
        destroy_skip_bb = builder.append_basic_block(name="destroy_skip")
        destroy_loop_end_bb = builder.append_basic_block(name="destroy_loop_end")

        builder.branch(destroy_loop_cond_bb)

        # Loop condition: i < old_capacity
        builder.position_at_end(destroy_loop_cond_bb)
        i_val = builder.load(loop_i, name="i_val")
        loop_cond = builder.icmp_unsigned("<", i_val, old_capacity, name="loop_cond")
        builder.cbranch(loop_cond, destroy_loop_body_bb, destroy_loop_end_bb)

        # Loop body: check if entry is occupied
        builder.position_at_end(destroy_loop_body_bb)
        i_val = builder.load(loop_i, name="i_val")
        entry_ptr = builder.gep(old_buckets_data, [i_val], name="entry_ptr")
        state_ptr = builder.gep(entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="state_ptr")
        state = builder.load(state_ptr, name="state")

        builder.branch(destroy_check_occupied_bb)

        builder.position_at_end(destroy_check_occupied_bb)
        is_occupied = builder.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), name="is_occupied")
        builder.cbranch(is_occupied, destroy_entry_bb, destroy_skip_bb)

        # Destroy occupied entry: recursively destroy key and value
        builder.position_at_end(destroy_entry_bb)

        # Get pointers to key and value
        key_ptr = builder.gep(entry_ptr, [zero_i32, zero_i32], name="key_ptr")
        value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="value_ptr")

        # Recursively destroy key and value using the general destructor
        from backend.destructors import emit_value_destructor
        emit_value_destructor(codegen, builder, key_ptr, key_type)
        emit_value_destructor(codegen, builder, value_ptr, value_type)

        builder.branch(destroy_skip_bb)

        # Skip non-occupied entries
        builder.position_at_end(destroy_skip_bb)
        i_next = builder.add(i_val, one_i32, name="i_next")
        builder.store(i_next, loop_i)
        builder.branch(destroy_loop_cond_bb)

        # After destroying all entries, free old buckets
        builder.position_at_end(destroy_loop_end_bb)

        # Free old buckets array
        old_buckets_void_ptr = builder.bitcast(old_buckets_data, ir.PointerType(codegen.types.i8), name="old_buckets_void_ptr")
        free_func = codegen.get_free_func()
        builder.call(free_func, [old_buckets_void_ptr])

    # Reset all fields to 0/null (HashMap is unusable)
    builder.store(zero_i32, size_ptr)
    builder.store(zero_i32, capacity_ptr)
    builder.store(zero_i32, tombstones_ptr)
    builder.store(null_entry_ptr, buckets_data_ptr)

    # Return unit (~)
    return ir.Constant(codegen.types.i32, 0)
