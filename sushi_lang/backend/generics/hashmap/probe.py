"""The linear-probe loop, and the key lookup over it, once."""

from typing import Any, Callable, NamedTuple, Optional

import llvmlite.ir as ir

from sushi_lang.backend.constants import ENTRY_KEY_INDICES, ENTRY_STATE_INDICES
from sushi_lang.semantics.typesys import Type
from .types import ENTRY_EMPTY, ENTRY_OCCUPIED, emit_key_hash_i32
from .utils import emit_key_equality_check
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
from sushi_lang.backend.generics.maybe import emit_maybe_none, emit_maybe_some
from sushi_lang.internals.errors import raise_internal_error


class ProbeSlot(NamedTuple):
    """The slot a probe step landed on."""
    entry_ptr: ir.Value   # Entry<K, V>* for this slot
    index: ir.Value       # i32 bucket index
    continue_bb: ir.Block  # branch here to probe the next slot


SlotFn = Callable[[ProbeSlot], None]


def emit_probe_loop(
    codegen: Any,
    buckets_data: ir.Value,
    capacity: ir.Value,
    hash_i32: ir.Value,
    *,
    on_empty: SlotFn,
    on_occupied: Optional[SlotFn] = None,
    on_tombstone: Optional[SlotFn] = None,
    exhausted_bb: Optional[ir.Block] = None,
    prefix: str = "probe",
) -> None:
    """Linear-probe the buckets from `hash_i32`, dispatching on each slot's state.

    A slot state with no handler probes the next slot.
    """
    builder = codegen.builder
    i32 = codegen.types.i32
    i8 = codegen.types.i8
    one = ir.Constant(i32, 1)

    probe_offset = entry_alloca(builder, i32, name=f"{prefix}_offset")
    builder.store(ir.Constant(i32, 0), probe_offset)

    loop_bb = builder.append_basic_block(name=f"{prefix}_loop")
    empty_bb = builder.append_basic_block(name=f"{prefix}_empty")
    occupied_bb = builder.append_basic_block(name=f"{prefix}_occupied")
    tombstone_bb = builder.append_basic_block(name=f"{prefix}_tombstone")
    continue_bb = builder.append_basic_block(name=f"{prefix}_continue")

    builder.branch(loop_bb)

    builder.position_at_end(loop_bb)
    offset = builder.load(probe_offset, name=f"{prefix}_offset_val")

    if exhausted_bb is not None:
        within_bb = builder.append_basic_block(name=f"{prefix}_within_limit")
        limit_reached = builder.icmp_signed(">=", offset, capacity, name=f"{prefix}_limit_reached")
        builder.cbranch(limit_reached, exhausted_bb, within_bb)
        builder.position_at_end(within_bb)

    # index = (hash + offset) & (capacity - 1) -- an AND, not a modulo, which is
    # only correct because every capacity in the growth table is a power of two.
    hash_plus_offset = builder.add(hash_i32, offset, name="hash_plus_offset")
    capacity_minus_1 = builder.sub(capacity, one, name="capacity_minus_1")
    index = builder.and_(hash_plus_offset, capacity_minus_1, name="index")

    entry_ptr = builder.gep(buckets_data, [index], name="entry_ptr")
    state_ptr = builder.gep(entry_ptr, ENTRY_STATE_INDICES, name="state_ptr")
    state = builder.load(state_ptr, name="state")

    is_empty = builder.icmp_unsigned("==", state, ir.Constant(i8, ENTRY_EMPTY), name="is_empty")
    check_occupied_bb = builder.append_basic_block(name=f"{prefix}_check_occupied")
    builder.cbranch(is_empty, empty_bb, check_occupied_bb)

    builder.position_at_end(check_occupied_bb)
    is_occupied = builder.icmp_unsigned("==", state, ir.Constant(i8, ENTRY_OCCUPIED), name="is_occupied")
    builder.cbranch(is_occupied, occupied_bb, tombstone_bb)

    slot = ProbeSlot(entry_ptr=entry_ptr, index=index, continue_bb=continue_bb)

    builder.position_at_end(empty_bb)
    on_empty(slot)
    _probe_on(builder, continue_bb)

    builder.position_at_end(occupied_bb)
    if on_occupied is not None:
        on_occupied(slot)
    _probe_on(builder, continue_bb)

    builder.position_at_end(tombstone_bb)
    if on_tombstone is not None:
        on_tombstone(slot)
    _probe_on(builder, continue_bb)

    builder.position_at_end(continue_bb)
    offset = builder.load(probe_offset, name=f"{prefix}_offset_val")
    builder.store(builder.add(offset, one, name=f"{prefix}_offset_next"), probe_offset)
    builder.branch(loop_bb)


def _probe_on(builder: ir.IRBuilder, continue_bb: ir.Block) -> None:
    """Probe the next slot, unless the handler already left the loop."""
    if builder.block.terminator is None:
        builder.branch(continue_bb)


class KeyLookup(NamedTuple):
    """Where a key lookup goes. `entry_ptr` and `entry_key_ptr` are valid in `found` only."""
    found: ir.Block
    not_found: ir.Block
    done: ir.Block
    entry_ptr: ir.Value
    entry_key_ptr: ir.Value


def emit_find_key(
    codegen: Any,
    buckets_data: ir.Value,
    capacity: ir.Value,
    key_type: Type,
    key_value: ir.Value,
    op: str,
) -> KeyLookup:
    """Probe for `key_value` and branch to `{op}_found` or `{op}_not_found`.

    Both blocks are empty; the caller fills them and joins them in `{op}_done`.
    """
    builder = codegen.builder
    hash_i32 = emit_key_hash_i32(codegen, key_type, key_value)

    found_bb = builder.append_basic_block(name=f"{op}_found")
    not_found_bb = builder.append_basic_block(name=f"{op}_not_found")
    done_bb = builder.append_basic_block(name=f"{op}_done")

    # The matching slot, captured out of the probe. It dominates found_bb, because
    # that block is only reachable from the probe.
    matched: dict[str, ir.Value] = {}

    def on_empty(slot: ProbeSlot) -> None:
        # A never-used slot ends the chain: the key was never here.
        builder.branch(not_found_bb)

    def on_occupied(slot: ProbeSlot) -> None:
        entry_key_ptr = builder.gep(slot.entry_ptr, ENTRY_KEY_INDICES, name="entry_key_ptr")
        entry_key = builder.load(entry_key_ptr, name="entry_key")
        keys_equal = emit_key_equality_check(codegen, key_type, key_value, entry_key)
        matched["entry_ptr"] = slot.entry_ptr
        matched["entry_key_ptr"] = entry_key_ptr
        builder.cbranch(keys_equal, found_bb, slot.continue_bb)

    emit_probe_loop(
        codegen, buckets_data, capacity, hash_i32,
        on_occupied=on_occupied, on_empty=on_empty,
        exhausted_bb=not_found_bb, prefix=f"{op}_probe",
    )

    return KeyLookup(found_bb, not_found_bb, done_bb,
                     matched["entry_ptr"], matched["entry_key_ptr"])


def emit_lookup_key(codegen: Any, expr: Any, key_type: Type, method: str) -> ir.Value:
    """The one key argument of a lookup, borrowed: a lookup reads the key and keeps nothing."""
    if len(expr.args) != 1:
        raise_internal_error("CE0023", method=method, expected=1, got=len(expr.args))
    return emit_borrowed_arg(codegen, expr.args[0], key_type)


def emit_lookup_maybe(
    codegen: Any,
    lookup: KeyLookup,
    value_type: Type,
    found_value: ir.Value,
    name: str,
) -> ir.Value:
    """Join a lookup into `Maybe.Some(found_value)` or `Maybe.None`.

    The builder is at the end of the found path, where `found_value` is ready.
    """
    builder = codegen.builder
    maybe_some = emit_maybe_some(codegen, value_type, found_value)
    some_pred_bb = builder.block
    builder.branch(lookup.done)

    builder.position_at_end(lookup.not_found)
    maybe_none = emit_maybe_none(codegen, value_type)
    builder.branch(lookup.done)

    builder.position_at_end(lookup.done)
    result_phi = builder.phi(maybe_some.type, name=name)
    result_phi.add_incoming(maybe_some, some_pred_bb)
    result_phi.add_incoming(maybe_none, lookup.not_found)
    return result_phi
