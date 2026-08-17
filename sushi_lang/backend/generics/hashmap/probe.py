"""The linear-probe loop, once."""

from typing import Any, Callable, NamedTuple, Optional

import llvmlite.ir as ir

from sushi_lang.backend.constants import ENTRY_STATE_INDICES
from .types import ENTRY_EMPTY, ENTRY_OCCUPIED


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
    on_occupied: SlotFn,
    on_empty: SlotFn,
    on_tombstone: Optional[SlotFn] = None,
    exhausted_bb: Optional[ir.Block] = None,
    prefix: str = "probe",
) -> None:
    """Linear-probe the buckets from `hash_i32`, dispatching on each slot's state."""
    builder = codegen.builder
    i32 = codegen.types.i32
    i8 = codegen.types.i8
    one = ir.Constant(i32, 1)

    probe_offset = builder.alloca(i32, name=f"{prefix}_offset")
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
