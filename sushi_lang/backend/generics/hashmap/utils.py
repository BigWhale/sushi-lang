"""Utility functions for HashMap<K, V> implementation."""

from typing import Any
from sushi_lang.semantics.typesys import Type
import llvmlite.ir as ir
from .types import ENTRY_OCCUPIED
from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_VALUE_INDICES, ENTRY_STATE_INDICES


def emit_insert_entry(codegen: Any, entry_ptr: ir.Value, key: ir.Value, value: ir.Value, entry_type: ir.Type) -> None:
    """Emit LLVM IR to insert a key-value pair into an entry slot."""
    builder = codegen.builder

    key_ptr = builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
    builder.store(key, key_ptr)

    value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="entry_value_ptr")
    builder.store(value, value_ptr)

    state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="entry_state_ptr")
    builder.store(ir.Constant(codegen.types.i8, ENTRY_OCCUPIED), state_ptr)


def emit_entry_state_check(codegen: Any, entry_ptr: ir.Value, state: int, name: str) -> ir.Value:
    """Emit `entry.state == <state>` for one of the ENTRY_* constants."""
    builder = codegen.builder
    state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
    loaded = builder.load(state_ptr, name="state")
    return builder.icmp_unsigned("==", loaded, ir.Constant(codegen.types.i8, state), name=name)


def emit_destroy_all_entries(
    codegen: Any,
    buckets_data: ir.Value,
    capacity: ir.Value,
    key_type: Type,
    value_type: Type,
    *,
    null_guard: bool = False,
) -> None:
    """Recursively destroy the key and value of every occupied bucket."""
    from sushi_lang.backend.destructors import emit_value_destructor
    from sushi_lang.backend.generics.container_walk import emit_container_walk

    builder = codegen.builder

    def occupied(entry_ptr: ir.Value, _index: ir.Value) -> ir.Value:
        return emit_entry_state_check(codegen, entry_ptr, ENTRY_OCCUPIED, "is_occupied")

    def destroy(entry_ptr: ir.Value, _index: ir.Value) -> None:
        key_ptr = builder.gep(entry_ptr, ENTRY_KEY_INDICES, name="key_ptr")
        value_ptr = builder.gep(entry_ptr, ENTRY_VALUE_INDICES, name="value_ptr")
        emit_value_destructor(codegen, key_ptr, key_type)
        emit_value_destructor(codegen, value_ptr, value_type)

    emit_container_walk(
        codegen, buckets_data, capacity, destroy,
        should_visit=occupied, null_guard=null_guard, prefix="destroy",
    )


def emit_init_buckets_empty(codegen: Any, buckets_data: ir.Value, capacity: ir.Value) -> None:
    """Mark every bucket EMPTY. Fresh malloc'd storage holds garbage, not zeroes."""
    from sushi_lang.backend.generics.container_walk import emit_container_walk
    from .types import ENTRY_EMPTY

    builder = codegen.builder

    def set_empty(entry_ptr: ir.Value, _index: ir.Value) -> None:
        state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
        builder.store(ir.Constant(codegen.types.i8, ENTRY_EMPTY), state_ptr)

    emit_container_walk(codegen, buckets_data, capacity, set_empty, prefix="init")
