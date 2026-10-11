"""The four HashMap<K, V> iterators: `keys()`, `values()`, `entries()`, `pairs()`.

Each walks the buckets, `{Entry<K, V>[] buckets, i32 size, i32 capacity, i32 tombstones}`,
and steps over a bucket that holds no entry. The cursor is the bucket index, the capacity
and the bucket data; `backend/runtime/iterators.py` makes the value of it (#1259).
"""

from typing import Any
import llvmlite.ir as ir
from sushi_lang.semantics.ast import MethodCall
from sushi_lang.semantics.typesys import StructType, Type
from sushi_lang.backend import gep_utils
from sushi_lang.semantics.generics.hashmap import parse_hashmap_types, ensure_entry_type_in_struct_table
from sushi_lang.semantics.generics.tuples import intern_tuple
from sushi_lang.internals.errors import raise_internal_error

# The fields of a HashMap and of its bucket descriptor.
_BUCKETS, _CAPACITY = 0, 2
_KEY, _VALUE, _STATE = 0, 1, 2


def emit_hashmap_keys(codegen: Any, call: MethodCall, hashmap_value: ir.Value,
                      hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.keys() -> Iterator<peek K>"""
    return _emit_bucket_iterator(codegen, call, hashmap_value, hashmap_type)


def emit_hashmap_values(codegen: Any, call: MethodCall, hashmap_value: ir.Value,
                        hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.values() -> Iterator<peek V>"""
    return _emit_bucket_iterator(codegen, call, hashmap_value, hashmap_type)


def emit_hashmap_entries(codegen: Any, call: MethodCall, hashmap_value: ir.Value,
                         hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.entries() -> Iterator<peek Entry<K, V>>"""
    return _emit_bucket_iterator(codegen, call, hashmap_value, hashmap_type)


def emit_hashmap_pairs(codegen: Any, call: MethodCall, hashmap_value: ir.Value,
                       hashmap_type: StructType) -> ir.Value:
    """Emit HashMap<K, V>.pairs() -> Iterator<(peek K, peek V)>"""
    return _emit_bucket_iterator(codegen, call, hashmap_value, hashmap_type)


def bucket_item_type(codegen: Any, hashmap_type: StructType, method: str) -> Type:
    """The item of a HashMap walk: the key, the value, an `Entry<K, V>` or `(K, V)`."""
    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    if method == "keys":
        return key_type
    if method == "values":
        return value_type
    if method == "entries":
        return ensure_entry_type_in_struct_table(
            codegen.struct_table, codegen.derived_methods, key_type, value_type)
    if method == "pairs":
        return intern_tuple(codegen.struct_table, codegen.enum_table, (key_type, value_type))
    raise_internal_error("CE0085", method=method)
    raise AssertionError("unreachable")


def bucket_source(codegen: Any, hashmap_type: StructType, method: str):
    """The cursor over the buckets of a map, for one of the four methods."""
    from sushi_lang.backend.generics.hashmap.types import ENTRY_OCCUPIED, get_entry_type
    from sushi_lang.backend.lifecycle import lifecycle_symbol
    from sushi_lang.backend.runtime.iterators import CursorSource

    key_type, value_type = parse_hashmap_types(hashmap_type, codegen, on_missing="raise")
    item_type = bucket_item_type(codegen, hashmap_type, method)

    def entry(codegen: Any, data: ir.Value, index: ir.Value) -> ir.Value:
        return codegen.builder.gep(data, [index], name="bucket")

    def live(codegen: Any, data: ir.Value, index: ir.Value) -> ir.Value:
        b = codegen.builder
        state = b.load(gep_utils.gep_struct_field(codegen, entry(codegen, data, index),
                                                  _STATE, "bucket_state_ptr"),
                       name="bucket_state")
        return b.icmp_unsigned("==", state, ir.Constant(codegen.types.i8, ENTRY_OCCUPIED),
                               name="occupied")

    def item(codegen: Any, data: ir.Value, index: ir.Value) -> ir.Value:
        b = codegen.builder
        bucket = entry(codegen, data, index)
        if method in ("keys", "values"):
            field = _KEY if method == "keys" else _VALUE
            return b.load(gep_utils.gep_struct_field(codegen, bucket, field, "field_ptr"),
                          name=method)
        key = b.load(gep_utils.gep_struct_field(codegen, bucket, _KEY, "key_ptr"), name="key")
        value = b.load(gep_utils.gep_struct_field(codegen, bucket, _VALUE, "value_ptr"),
                       name="value")
        pair = ir.Constant(codegen.types.ll_type(item_type), ir.Undefined)
        pair = b.insert_value(pair, key, 0, name="pair_key")
        return b.insert_value(pair, value, 1, name="pair")

    return CursorSource(key=lifecycle_symbol(f"{method}_", hashmap_type),
                        item_type=item_type,
                        data_type=get_entry_type(codegen, key_type, value_type),
                        item=item, live=live)


def bucket_slots(codegen: Any, hashmap_ptr: ir.Value) -> tuple[ir.Value, ir.Value]:
    """The capacity of a map at an address, and its bucket data."""
    b = codegen.builder
    capacity = b.load(gep_utils.gep_struct_field(codegen, hashmap_ptr, _CAPACITY,
                                                 "capacity_ptr"), name="capacity")
    buckets = gep_utils.gep_struct_field(codegen, hashmap_ptr, _BUCKETS, "buckets_ptr")
    data = b.load(gep_utils.gep_dynamic_array_data(codegen, buckets), name="buckets_data")
    return capacity, data


def _emit_bucket_iterator(codegen: Any, call: MethodCall, hashmap_value: ir.Value,
                          hashmap_type: StructType) -> ir.Value:
    """The one producer of the four methods: a cursor, a lent value or an owned value."""
    from sushi_lang.backend.runtime.iterators import OwnedContainer, emit_cursor_iterator

    if len(call.args) != 0:
        raise_internal_error("CE0023", method=call.method, expected=0, got=len(call.args))
    address = _hashmap_address(codegen, hashmap_value)
    capacity, data = bucket_slots(codegen, address)

    def owned() -> OwnedContainer:
        from sushi_lang.backend.expressions.calls.dispatcher import consume_receiver
        value = consume_receiver(codegen, call, codegen.builder.load(address, name="map"))
        return OwnedContainer(value=value, semantic_type=hashmap_type, slots=bucket_slots)

    return emit_cursor_iterator(codegen, call, bucket_source(codegen, hashmap_type,
                                                             call.method),
                                capacity, data, owned)


def _hashmap_address(codegen: Any, hashmap_value: ir.Value) -> ir.Value:
    """The receiver arrives as an address, or as a value when it is a call result."""
    if isinstance(hashmap_value.type, ir.PointerType):
        return hashmap_value
    from sushi_lang.backend.memory.allocas import entry_alloca
    slot = entry_alloca(codegen.builder, hashmap_value.type, name="map_receiver")
    codegen.builder.store(hashmap_value, slot)
    return slot
