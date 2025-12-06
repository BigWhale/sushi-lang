"""
HashMap<K, V> core method implementations.

This module contains the core HashMap methods for basic operations and lookups:
- new, len, is_empty, tombstone_count (simple operations)
- get, contains_key (lookup operations)
"""

from typing import Any
from semantics.ast import MethodCall, Name
from semantics.typesys import StructType, BuiltinType
import llvmlite.ir as ir
from ..types import get_entry_type, extract_key_value_types, ENTRY_EMPTY, ENTRY_OCCUPIED
from ..utils import emit_key_equality_check
from internals.errors import raise_internal_error


def emit_hashmap_new(codegen: Any, hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.new() -> HashMap<K, V>

    Creates an empty HashMap with initial capacity of 16 buckets.

    Implementation:
    1. Allocate Entry<K, V>[] array with 16 elements
    2. Initialize all entries to Empty state (state = 0)
    3. Create HashMap struct: {buckets, size=0, capacity=16, tombstones=0}

    Args:
        codegen: LLVM codegen instance.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        HashMap<K, V> struct value.
    """
    # Extract K and V types
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    # Get LLVM types
    entry_type = get_entry_type(codegen, key_type, value_type)
    # Use cached type from type system to ensure consistency
    hashmap_llvm_type = codegen.types.ll_type(hashmap_type)

    # Initial capacity
    initial_capacity = 16
    capacity_const = ir.Constant(codegen.types.i32, initial_capacity)

    # Allocate buckets array: malloc(sizeof(Entry<K,V>) * capacity)
    # Get size of Entry type using LLVM's size_of intrinsic pattern
    # sizeof(T) = ptrtoint (T* getelementptr (T* null, 1))
    null_ptr = ir.Constant(ir.PointerType(entry_type), None)
    one = ir.Constant(codegen.types.i32, 1)
    gep = codegen.builder.gep(null_ptr, [one], name="sizeof_gep")
    entry_size = codegen.builder.ptrtoint(gep, codegen.types.i32, name="entry_size")
    total_bytes = codegen.builder.mul(entry_size, capacity_const, name="bucket_bytes")

    # Call malloc (extend i32 to i64 for malloc parameter)
    malloc_fn = codegen.get_malloc_func()
    total_bytes_i64 = codegen.builder.zext(total_bytes, ir.IntType(64), name="total_bytes_i64")
    bucket_ptr_i8 = codegen.builder.call(malloc_fn, [total_bytes_i64], name="buckets_raw")
    bucket_ptr = codegen.builder.bitcast(bucket_ptr_i8, ir.PointerType(entry_type), name="buckets_ptr")

    # Initialize all entries to Empty state
    # Loop through buckets and set state = ENTRY_EMPTY (0)
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    i = codegen.builder.alloca(codegen.types.i32, name="i")
    codegen.builder.store(zero_i32, i)

    loop_cond_bb = codegen.builder.append_basic_block(name="init_loop_cond")
    loop_body_bb = codegen.builder.append_basic_block(name="init_loop_body")
    loop_end_bb = codegen.builder.append_basic_block(name="init_loop_end")

    codegen.builder.branch(loop_cond_bb)

    # Loop condition: i < capacity
    codegen.builder.position_at_end(loop_cond_bb)
    i_val = codegen.builder.load(i, name="i_val")
    cond = codegen.builder.icmp_unsigned("<", i_val, capacity_const, name="loop_cond")
    codegen.builder.cbranch(cond, loop_body_bb, loop_end_bb)

    # Loop body: set entry[i].state = ENTRY_EMPTY
    codegen.builder.position_at_end(loop_body_bb)
    i_val = codegen.builder.load(i, name="i_val")
    entry_ptr = codegen.builder.gep(bucket_ptr, [i_val], name="entry_ptr")
    state_ptr = codegen.builder.gep(entry_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="state_ptr")  # state is field 2 in Entry
    codegen.builder.store(ir.Constant(codegen.types.i8, ENTRY_EMPTY), state_ptr)

    # i++
    i_next = codegen.builder.add(i_val, ir.Constant(codegen.types.i32, 1), name="i_next")
    codegen.builder.store(i_next, i)
    codegen.builder.branch(loop_cond_bb)

    # After loop
    codegen.builder.position_at_end(loop_end_bb)

    # Create dynamic array struct for buckets: {len, cap, data}
    buckets_array_type = ir.LiteralStructType([codegen.types.i32, codegen.types.i32, ir.PointerType(entry_type)])
    buckets_array = ir.Constant(buckets_array_type, ir.Undefined)
    buckets_array = codegen.builder.insert_value(buckets_array, capacity_const, 0, name="buckets_len")
    buckets_array = codegen.builder.insert_value(buckets_array, capacity_const, 1, name="buckets_cap")
    buckets_array = codegen.builder.insert_value(buckets_array, bucket_ptr, 2, name="buckets_data")

    # Create HashMap struct: {buckets, size, capacity, tombstones}
    result = ir.Constant(hashmap_llvm_type, ir.Undefined)
    result = codegen.builder.insert_value(result, buckets_array, 0, name="hm_buckets")
    result = codegen.builder.insert_value(result, zero_i32, 1, name="hm_size")
    result = codegen.builder.insert_value(result, capacity_const, 2, name="hm_capacity")
    result = codegen.builder.insert_value(result, zero_i32, 3, name="hm_tombstones")

    return result


def emit_hashmap_len(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.len() -> i32

    Returns the number of occupied entries (excludes tombstones).

    Implementation:
    1. Load size field from HashMap struct

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.

    Returns:
        i32 size value.
    """
    # Get pointer to size field (index 1: buckets at 0, size at 1)
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    one_i32 = ir.Constant(codegen.types.i32, 1)
    size_ptr = builder.gep(hashmap_value, [zero_i32, one_i32], name="size_ptr")
    return builder.load(size_ptr, name="hashmap_size")


def emit_hashmap_is_empty(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.is_empty() -> bool

    Returns true if size == 0.

    Implementation:
    1. Extract size field
    2. Compare size == 0

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct value.

    Returns:
        bool value (i1 or i32 depending on context).
    """
    size = emit_hashmap_len(codegen, hashmap_value)
    zero = ir.Constant(codegen.types.i32, 0)
    return codegen.builder.icmp_signed("==", size, zero, name="is_empty")


def emit_hashmap_tombstone_count(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """Emit HashMap<K, V>.tombstone_count() -> i32

    Returns the number of deleted entries.

    Implementation:
    1. Load tombstones field from HashMap struct

    Args:
        codegen: LLVM codegen instance.
        hashmap_value: The HashMap struct pointer.

    Returns:
        i32 tombstones value.
    """
    # Get pointer to tombstones field (index 3: buckets at 0, size at 1, capacity at 2, tombstones at 3)
    builder = codegen.builder
    zero_i32 = ir.Constant(codegen.types.i32, 0)
    three_i32 = ir.Constant(codegen.types.i32, 3)
    tombstones_ptr = builder.gep(hashmap_value, [zero_i32, three_i32], name="tombstones_ptr")
    return builder.load(tombstones_ptr, name="hashmap_tombstones")


def emit_hashmap_get(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.get(K key) -> Maybe<V>

    Retrieves a value from the HashMap by key.

    Algorithm:
    1. Hash the key to get hash value
    2. Linear probe to find slot:
       - If Occupied with matching key: return Maybe.Some(value)
       - If Empty: return Maybe.None() (not found)
       - If Tombstone or occupied with different key: continue probing
    3. Return Maybe<V>

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with key argument.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        Maybe<V> struct value.
    """
    from stdlib.src.common import get_builtin_method
    from semantics.ast import MethodCall, Name
    from semantics.typesys import BuiltinType
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
        raise_internal_error("CE0023", method="get", expected=1, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])

    # Get HashMap fields
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    # Get buckets array pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Hash the key (register on-demand if needed for array types)
    from ..types import get_key_hash_method
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

    probe_loop_bb = builder.append_basic_block(name="get_probe_loop")
    probe_body_bb = builder.append_basic_block(name="get_probe_body")
    probe_occupied_bb = builder.append_basic_block(name="get_probe_occupied")
    probe_empty_bb = builder.append_basic_block(name="get_probe_empty")
    probe_continue_bb = builder.append_basic_block(name="get_probe_continue")
    found_bb = builder.append_basic_block(name="get_found")
    not_found_bb = builder.append_basic_block(name="get_not_found")
    get_done_bb = builder.append_basic_block(name="get_done")

    builder.branch(probe_loop_bb)

    # Probe loop
    builder.position_at_end(probe_loop_bb)
    probe_offset_val = builder.load(probe_offset, name="probe_offset_val")

    # Check if we've probed all slots (probe_offset >= capacity)
    probe_limit_reached = builder.icmp_signed(">=", probe_offset_val, capacity, name="probe_limit_reached")
    probe_within_limit_bb = builder.append_basic_block(name="probe_within_limit")
    builder.cbranch(probe_limit_reached, not_found_bb, probe_within_limit_bb)

    # Continue probing within limit
    builder.position_at_end(probe_within_limit_bb)

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
    check_occupied_bb = builder.append_basic_block(name="check_occupied")
    builder.cbranch(is_empty, probe_empty_bb, check_occupied_bb)

    # Check if occupied
    builder.position_at_end(check_occupied_bb)
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

    # Found: return Maybe.Some(value)
    builder.position_at_end(found_bb)
    entry_value_ptr = builder.gep(entry_ptr, [zero_i32, one_i32], name="entry_value_ptr")
    entry_value = builder.load(entry_value_ptr, name="entry_value")

    # Create Maybe<V> enum for return
    # Get the Maybe<V> enum type from the generic enum table
    from semantics.generics.types import GenericEnumType
    from semantics.typesys import BuiltinType

    # Get the Maybe<V> monomorphized enum type
    # We need to look it up in the codegen.enum_table
    # Format the type name properly
    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    maybe_enum_name = f"Maybe<{type_str}>"
    maybe_enum_type = codegen.enum_table.by_name.get(maybe_enum_name)

    if maybe_enum_type is None:
        # Maybe<V> not monomorphized - create it on the fly
        # This happens when HashMap.get() is used but Maybe<V> wasn't used elsewhere
        from backend.generics.maybe import ensure_maybe_type_exists
        maybe_enum_type = ensure_maybe_type_exists(codegen, value_type)
        if maybe_enum_type is None:
            # Still couldn't create it - this shouldn't happen
            available = list(codegen.enum_table.by_name.keys())
            raise_internal_error("CE0047", type=type_str)

    # Get LLVM type for Maybe<V> enum: {i32 tag, [N x i8] data}
    maybe_llvm_type = codegen.types.get_enum_type(maybe_enum_type)

    # Create Maybe.Some(value)
    # Tag 0 = Some, Tag 1 = None (based on variant order in Maybe definition)
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

    builder.branch(get_done_bb)

    # Not found: return Maybe.None()
    builder.position_at_end(not_found_bb)
    maybe_none = ir.Constant(maybe_llvm_type, ir.Undefined)
    none_tag = ir.Constant(codegen.types.i32, 1)  # None is second variant
    maybe_none = builder.insert_value(maybe_none, none_tag, 0, name="maybe_none_tag")
    # Data field is undefined for None
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    maybe_none = builder.insert_value(maybe_none, undef_data, 1, name="maybe_none_data")
    builder.branch(get_done_bb)

    # Continue probing (tombstone or different key)
    builder.position_at_end(probe_continue_bb)
    probe_offset_next = builder.add(probe_offset_val, one_i32, name="probe_offset_next")
    builder.store(probe_offset_next, probe_offset)
    builder.branch(probe_loop_bb)

    # Done: merge results
    builder.position_at_end(get_done_bb)
    result_phi = builder.phi(maybe_llvm_type, name="get_result")
    result_phi.add_incoming(maybe_some, found_bb)
    result_phi.add_incoming(maybe_none, not_found_bb)

    return result_phi


def emit_hashmap_contains_key(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.contains_key(K key) -> bool

    Checks if a key exists in the HashMap.

    Algorithm:
    1. Hash the key to get hash value
    2. Linear probe to find slot:
       - If Occupied with matching key: return true
       - If Empty: return false (not found)
       - If Tombstone or occupied with different key: continue probing
    3. Return bool

    Args:
        codegen: LLVM codegen instance.
        expr: The method call expression with key argument.
        hashmap_value: The HashMap struct pointer.
        hashmap_type: The HashMap<K, V> struct type.

    Returns:
        bool value (i32 0 or 1).
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
    true_val = ir.Constant(codegen.types.i32, 1)
    false_val = ir.Constant(codegen.types.i32, 0)

    # Emit the key argument
    if len(expr.args) != 1:
        raise_internal_error("CE0023", method="contains_key", expected=1, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])

    # Get HashMap fields
    capacity_ptr = builder.gep(hashmap_value, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    # Get buckets array pointer
    buckets_ptr = builder.gep(hashmap_value, [zero_i32, zero_i32], name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, [zero_i32, ir.Constant(codegen.types.i32, 2)], name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Hash the key (register on-demand if needed for array types)
    from ..types import get_key_hash_method
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

    probe_loop_bb = builder.append_basic_block(name="contains_probe_loop")
    probe_body_bb = builder.append_basic_block(name="contains_probe_body")
    probe_occupied_bb = builder.append_basic_block(name="contains_probe_occupied")
    probe_empty_bb = builder.append_basic_block(name="contains_probe_empty")
    probe_continue_bb = builder.append_basic_block(name="contains_probe_continue")
    found_bb = builder.append_basic_block(name="contains_found")
    not_found_bb = builder.append_basic_block(name="contains_not_found")
    contains_done_bb = builder.append_basic_block(name="contains_done")

    builder.branch(probe_loop_bb)

    # Probe loop
    builder.position_at_end(probe_loop_bb)
    probe_offset_val = builder.load(probe_offset, name="probe_offset_val")

    # Check if we've probed all slots (probe_offset >= capacity)
    probe_limit_reached = builder.icmp_signed(">=", probe_offset_val, capacity, name="probe_limit_reached")
    probe_within_limit_bb = builder.append_basic_block(name="probe_within_limit")
    builder.cbranch(probe_limit_reached, not_found_bb, probe_within_limit_bb)

    # Continue probing within limit
    builder.position_at_end(probe_within_limit_bb)

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
    check_occupied_bb = builder.append_basic_block(name="check_occupied")
    builder.cbranch(is_empty, probe_empty_bb, check_occupied_bb)

    # Check if occupied
    builder.position_at_end(check_occupied_bb)
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

    # Found: return true
    builder.position_at_end(found_bb)
    builder.branch(contains_done_bb)

    # Not found: return false
    builder.position_at_end(not_found_bb)
    builder.branch(contains_done_bb)

    # Continue probing (tombstone or different key)
    builder.position_at_end(probe_continue_bb)
    probe_offset_next = builder.add(probe_offset_val, one_i32, name="probe_offset_next")
    builder.store(probe_offset_next, probe_offset)
    builder.branch(probe_loop_bb)

    # Done: merge results
    builder.position_at_end(contains_done_bb)
    result_phi = builder.phi(codegen.types.i32, name="contains_result")
    result_phi.add_incoming(true_val, found_bb)
    result_phi.add_incoming(false_val, not_found_bb)

    return result_phi
