"""The linear-probe loop, once.

insert, remove, get, contains_key and the resize rehash all walk the bucket array
the same way: start at `hash & (capacity - 1)`, step forward one slot at a time,
and dispatch on the slot's state. That skeleton was written out five times.

What the five do *with* a slot is genuinely different -- get clones the value out,
remove leaves a tombstone, insert may reuse a tombstone it passed earlier, the
rehash exits into the loop above it -- so the callbacks own the slot bodies, and
the exit. This emits the walk, not the decision.
"""

from typing import Any, Callable, NamedTuple, Optional

import llvmlite.ir as ir

from sushi_lang.backend.constants import ENTRY_STATE_INDICES
from .types import ENTRY_EMPTY, ENTRY_OCCUPIED


class ProbeSlot(NamedTuple):
    """The slot a probe step landed on."""
    entry_ptr: ir.Value   # Entry<K, V>* for this slot
    index: ir.Value       # i32 bucket index
    continue_bb: ir.Block  # branch here to probe the next slot


# A slot handler. It may terminate its block (to leave the loop); if it does not,
# the loop continues probing.
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
    """Linear-probe the buckets from `hash_i32`, dispatching on each slot's state.

    The loop has no natural exit: a handler either terminates its block (branching
    to one of the caller's blocks) or falls through and the next slot is probed.
    So the caller owns every exit, and any result phi. On return the builder sits
    on a terminated block -- position it at your own exit before emitting more.

    Args:
        codegen: LLVM codegen instance.
        buckets_data: Pointer to the bucket storage (Entry<K, V>*). Passed in
            rather than read from the map, because the rehash probes storage that
            is not in the map yet.
        capacity: Bucket count. Must be a power of two: the index is masked with
            `capacity - 1` rather than reduced modulo it.
        hash_i32: The key's hash, truncated to i32.
        on_occupied: Slot holds a live entry. Typically compares keys.
        on_empty: Slot was never used. Terminates the probe for a lookup.
        on_tombstone: Slot held an entry that was removed. Defaults to probing on,
            which is what keeps a removed key's probe chain intact.
        exhausted_bb: Where to go once every slot has been probed. Without it a
            full table would probe forever.
        prefix: Block-name prefix, so nested probes stay readable in the IR.
    """
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
    """Probe the next slot, unless the handler already left the loop.

    Branches from wherever the builder ended up: a handler may append blocks of
    its own (emit_key_equality_check and emit_value_destructor both do).
    """
    if builder.block.terminator is None:
        builder.branch(continue_bb)
