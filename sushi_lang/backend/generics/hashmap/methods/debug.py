"""HashMap<K, V> debug method implementation."""

from typing import Any
from sushi_lang.semantics.typesys import StructType
import llvmlite.ir as ir
from ..types import get_hashmap_field_ptrs, ENTRY_EMPTY, ENTRY_OCCUPIED, ENTRY_TOMBSTONE
from sushi_lang.semantics.generics.hashmap import extract_key_value_types
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.backend.constants.llvm_values import ZERO_I32, make_i8_const
from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_VALUE_INDICES, ENTRY_STATE_INDICES
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.generics.debug_output import (
    emit_debug_string, emit_debug_i32, emit_debug_value
)


def emit_hashmap_debug(
    codegen: Any,
    hashmap_value: ir.Value,
    hashmap_type: StructType
) -> ir.Value:
    """Emit HashMap<K, V>.debug() -> ~"""
    builder = codegen.builder

    key_type, value_type = extract_key_value_types(hashmap_type, codegen)

    fields = get_hashmap_field_ptrs(codegen, hashmap_value)
    size_ptr, capacity_ptr = fields.size, fields.capacity
    tombstones_ptr, buckets_data_ptr = fields.tombstones, fields.buckets_data

    size = builder.load(size_ptr, name="size")
    capacity = builder.load(capacity_ptr, name="capacity")
    tombstones = builder.load(tombstones_ptr, name="tombstones")

    buckets_data = builder.load(buckets_data_ptr, name="buckets_data")

    # Print header: "HashMap@(K, V) {". Both types go through display_type, not
    # str(): a monomorphized type's str() is its interned `<...>` identity name, so
    # interpolating it directly leaks the retired syntax for a nested generic key
    # or value ("HashMap@(string, List<i32>)" instead of "...List@(i32))").
    header_str = f"HashMap@({display_type(key_type)}, {display_type(value_type)}) {{\n"
    emit_debug_string(codegen, builder, header_str)

    emit_debug_string(codegen, builder, "  size: ")
    emit_debug_i32(codegen, builder, size)
    emit_debug_string(codegen, builder, "\n")

    emit_debug_string(codegen, builder, "  capacity: ")
    emit_debug_i32(codegen, builder, capacity)
    emit_debug_string(codegen, builder, "\n")

    emit_debug_string(codegen, builder, "  tombstones: ")
    emit_debug_i32(codegen, builder, tombstones)
    emit_debug_string(codegen, builder, "\n")

    def print_entry(entry_ptr: ir.Value, index: ir.Value) -> None:
        state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
        state = builder.load(state_ptr, name="state")

        empty_bb = builder.append_basic_block(name="debug_empty")
        occupied_bb = builder.append_basic_block(name="debug_occupied")
        tombstone_bb = builder.append_basic_block(name="debug_tombstone")
        check_occupied_bb = builder.append_basic_block(name="debug_check_occupied")
        check_tombstone_bb = builder.append_basic_block(name="debug_check_tombstone")
        join_bb = builder.append_basic_block(name="debug_entry_done")

        is_empty = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_EMPTY), name="is_empty")
        builder.cbranch(is_empty, empty_bb, check_occupied_bb)

        builder.position_at_end(check_occupied_bb)
        is_occupied = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_OCCUPIED), name="is_occupied")
        builder.cbranch(is_occupied, occupied_bb, check_tombstone_bb)

        builder.position_at_end(check_tombstone_bb)
        is_tombstone = builder.icmp_unsigned("==", state, make_i8_const(ENTRY_TOMBSTONE), name="is_tombstone")
        builder.cbranch(is_tombstone, tombstone_bb, join_bb)

        builder.position_at_end(empty_bb)
        emit_debug_string(codegen, builder, "  [")
        emit_debug_i32(codegen, builder, index)
        emit_debug_string(codegen, builder, "] Empty\n")
        builder.branch(join_bb)

        builder.position_at_end(occupied_bb)
        emit_debug_string(codegen, builder, "  [")
        emit_debug_i32(codegen, builder, index)
        emit_debug_string(codegen, builder, "] Occupied: ")

        key_ptr = builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="key_ptr")
        key = builder.load(key_ptr, name="key")
        emit_debug_value(codegen, builder, key, key_type)

        emit_debug_string(codegen, builder, " -> ")

        value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="value_ptr")
        value = builder.load(value_ptr, name="value")
        emit_debug_value(codegen, builder, value, value_type)

        emit_debug_string(codegen, builder, "\n")
        builder.branch(join_bb)

        builder.position_at_end(tombstone_bb)
        emit_debug_string(codegen, builder, "  [")
        emit_debug_i32(codegen, builder, index)
        emit_debug_string(codegen, builder, "] Tombstone\n")
        builder.branch(join_bb)

        builder.position_at_end(join_bb)

    emit_container_walk(codegen, buckets_data, capacity, print_entry, prefix="debug")

    emit_debug_string(codegen, builder, "}\n")

    return ZERO_I32
