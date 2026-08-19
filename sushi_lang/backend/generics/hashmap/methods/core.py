"""HashMap<K, V> core method implementations."""

from typing import Any
from sushi_lang.semantics.ast import MethodCall, Name
from sushi_lang.semantics.typesys import StructType, BuiltinType
import llvmlite.ir as ir
from ..types import get_entry_type
from sushi_lang.backend.constants import (
    HASHMAP_BUCKETS_INDICES,
    HASHMAP_SIZE_INDICES,
    HASHMAP_CAPACITY_INDICES,
    HASHMAP_TOMBSTONES_INDICES,
    BUCKETS_DATA_INDICES,
    ENTRY_KEY_INDICES,
    ENTRY_VALUE_INDICES,
)
from sushi_lang.semantics.generics.hashmap import extract_key_value_types
from ..utils import emit_key_equality_check, emit_init_buckets_empty
from ..probe import emit_probe_loop, ProbeSlot
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.backend.memory.heap import emit_malloc
from sushi_lang.backend.expressions.memory import get_element_size_constant


def emit_hashmap_new(codegen: Any, hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.new() -> HashMap<K, V>"""
    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

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
    from sushi_lang.semantics.ast import MethodCall
    import sushi_lang.backend.types.primitives.hashing  # noqa: F401

    builder = codegen.builder

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    value_llvm = codegen.types.ll_type(value_type)

    if len(expr.args) != 1:
        raise_internal_error("CE0023", method="get", expected=1, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])

    capacity_ptr = builder.gep(hashmap_value, HASHMAP_CAPACITY_INDICES, name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    buckets_ptr = builder.gep(hashmap_value, HASHMAP_BUCKETS_INDICES, name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, BUCKETS_DATA_INDICES, name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    from ..types import get_key_hash_method
    hash_method = get_key_hash_method(codegen, key_type)
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

    found_bb = builder.append_basic_block(name="get_found")
    not_found_bb = builder.append_basic_block(name="get_not_found")
    get_done_bb = builder.append_basic_block(name="get_done")

    # The matching slot, captured out of the probe for the found path below. It
    # dominates found_bb -- that block is only reachable from the probe.
    matched: dict[str, ir.Value] = {}

    def on_empty(slot: ProbeSlot) -> None:
        # A never-used slot ends the chain: the key was never here.
        builder.branch(not_found_bb)

    def on_occupied(slot: ProbeSlot) -> None:
        matched["entry_ptr"] = slot.entry_ptr
        entry_key_ptr = builder.gep(slot.entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
        entry_key = builder.load(entry_key_ptr, name="entry_key")
        keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)
        builder.cbranch(keys_equal, found_bb, slot.continue_bb)

    emit_probe_loop(
        codegen, buckets_data, capacity, hash_i32,
        on_occupied=on_occupied, on_empty=on_empty,
        exhausted_bb=not_found_bb, prefix="get_probe",
    )

    builder.position_at_end(found_bb)
    entry_ptr = matched["entry_ptr"]
    entry_value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
    entry_value = builder.load(entry_value_ptr, name="entry_value")

    # `.get()` READS. It does not detach (#242): the entry stays OCCUPIED and `map.free()`
    # still destroys it, so the returned `Maybe.Some(V)` carries a BORROW. Pass 3
    # classifies it BORROWED, a `let` of it binds without owning, and a position that
    # takes ownership rejects it (CE2411) with `.clone()` as the escape. The deep copy
    # that used to happen here was the compiler inserting one the user did not ask for.

    if isinstance(value_type, BuiltinType):
        type_str = str(value_type).lower()
    else:
        type_str = str(value_type)

    maybe_enum_name = f"Maybe<{type_str}>"
    maybe_enum_type = codegen.enum_table.by_name.get(maybe_enum_name)

    if maybe_enum_type is None:
        from sushi_lang.backend.generics.maybe import ensure_maybe_type_exists
        maybe_enum_type = ensure_maybe_type_exists(codegen, value_type)
        if maybe_enum_type is None:
            raise_internal_error("CE0047", type=type_str)

    maybe_llvm_type = codegen.types.get_enum_type(maybe_enum_type)

    maybe_some = ir.Constant(maybe_llvm_type, ir.Undefined)
    some_tag = ir.Constant(codegen.types.i32, 0)  # Some is first variant
    maybe_some = builder.insert_value(maybe_some, some_tag, 0, name="maybe_some_tag")

    data_array_type = maybe_llvm_type.elements[1]  # [N x i8]
    data_ptr = builder.alloca(data_array_type, name="some_data_alloc")
    value_ptr = builder.bitcast(data_ptr, ir.PointerType(value_llvm), name="value_ptr")
    builder.store(entry_value, value_ptr)
    data_value = builder.load(data_ptr, name="some_data")
    maybe_some = builder.insert_value(maybe_some, data_value, 1, name="maybe_some_value")

    some_pred_bb = builder.block
    builder.branch(get_done_bb)

    builder.position_at_end(not_found_bb)
    maybe_none = ir.Constant(maybe_llvm_type, ir.Undefined)
    none_tag = ir.Constant(codegen.types.i32, 1)  # None is second variant
    maybe_none = builder.insert_value(maybe_none, none_tag, 0, name="maybe_none_tag")
    undef_data = ir.Constant(data_array_type, ir.Undefined)
    maybe_none = builder.insert_value(maybe_none, undef_data, 1, name="maybe_none_data")
    builder.branch(get_done_bb)

    builder.position_at_end(get_done_bb)
    result_phi = builder.phi(maybe_llvm_type, name="get_result")
    result_phi.add_incoming(maybe_some, some_pred_bb)
    result_phi.add_incoming(maybe_none, not_found_bb)

    return result_phi


def emit_hashmap_contains_key(
    codegen: Any,
    expr: MethodCall,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.contains_key(K key) -> bool"""
    from sushi_lang.semantics.ast import MethodCall
    import sushi_lang.backend.types.primitives.hashing  # noqa: F401

    builder = codegen.builder

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    true_val = ir.Constant(codegen.types.i32, 1)
    false_val = ir.Constant(codegen.types.i32, 0)

    if len(expr.args) != 1:
        raise_internal_error("CE0023", method="contains_key", expected=1, got=len(expr.args))

    key_value = codegen.expressions.emit_expr(expr.args[0])

    capacity_ptr = builder.gep(hashmap_value, HASHMAP_CAPACITY_INDICES, name="capacity_ptr")
    capacity = builder.load(capacity_ptr, name="capacity")

    buckets_ptr = builder.gep(hashmap_value, HASHMAP_BUCKETS_INDICES, name="buckets_ptr")
    buckets_data_ptr = builder.gep(buckets_ptr, BUCKETS_DATA_INDICES, name="buckets_data_ptr")
    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    from ..types import get_key_hash_method
    hash_method = get_key_hash_method(codegen, key_type)
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

    found_bb = builder.append_basic_block(name="contains_found")
    not_found_bb = builder.append_basic_block(name="contains_not_found")
    contains_done_bb = builder.append_basic_block(name="contains_done")

    def on_empty(slot: ProbeSlot) -> None:
        builder.branch(not_found_bb)

    def on_occupied(slot: ProbeSlot) -> None:
        entry_key_ptr = builder.gep(slot.entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
        entry_key = builder.load(entry_key_ptr, name="entry_key")
        keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)
        builder.cbranch(keys_equal, found_bb, slot.continue_bb)

    emit_probe_loop(
        codegen, buckets_data, capacity, hash_i32,
        on_occupied=on_occupied, on_empty=on_empty,
        exhausted_bb=not_found_bb, prefix="contains_probe",
    )

    builder.position_at_end(found_bb)
    builder.branch(contains_done_bb)

    builder.position_at_end(not_found_bb)
    builder.branch(contains_done_bb)

    builder.position_at_end(contains_done_bb)
    result_phi = builder.phi(codegen.types.i32, name="contains_result")
    result_phi.add_incoming(true_val, found_bb)
    result_phi.add_incoming(false_val, not_found_bb)

    return result_phi
