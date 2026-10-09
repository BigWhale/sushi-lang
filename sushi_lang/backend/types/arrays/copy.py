"""The one bulk array copy: `extend`, `extend_range` and `ss` (#462).

Three spellings over one emitter. `extend(src)` is `extend_range(src, 0, src.len())`, and
`ss(start, count)` is a fresh array of a run-time length plus the same range copy. Writing
the copy three times is what this module exists to prevent: each site would carry its own
bounds rule, its own per-element clone decision, and its own idea of what the source owns.

**A bulk write borrows its source, and every slot it writes takes its own `copy_out`.**
That is #478's Ruling 7 in its general form. A write that fills N slots cannot consume,
because consuming means one value reaching one position and a bulk write has no single
position. It covers one value and N slots, and it covers N source values and N slots.

A plain element type copies with a `memcpy` -- a shallow store of a plain value IS the
value, so the walk would emit N stores for no gain. An owning one walks and clones, the
decision `backend/lifecycle.py`'s handler table already makes through `copy_out`.

**A range outside the source is CLAMPED, never trapped.** `string.s` and `string.ss` have
always clamped, and these are their array twins, so they answer the same way: a start past
the end gives nothing, a run past the end stops at the end, and an end before the start
gives nothing. Clamping is also what makes the walk safe -- it compares with an unsigned
predicate, so a negative count would otherwise read as four billion and run off the buffer.
The clamp removes that by construction rather than by a guard that has to fire.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.utils.validation import require_builder

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_range_copy(codegen: 'LLVMCodegen', dest_data: ir.Value, source_data: ir.Value,
                    start: ir.Value, count: ir.Value, element_type: Optional['Type'],
                    element_llvm_type: ir.Type, prefix: str = "copy") -> None:
    """Copy `source_data[start .. start + count)` into `dest_data[0 .. count)`.

    The caller has already made room and checked the bounds. This is the copy alone.
    """
    from sushi_lang.backend.destructors import needs_cleanup

    source_base = gep_utils.gep_array_element(codegen, source_data, start, f"{prefix}_src")

    if element_type is None or not needs_cleanup(codegen, element_type):
        _emit_memcpy(codegen, dest_data, source_base, count, element_llvm_type)
        return

    from sushi_lang.backend.ownership import copy_out

    def clone_one(slot: ir.Value, index: ir.Value) -> None:
        source_slot = gep_utils.gep_array_element(codegen, source_base, index)
        held = codegen.builder.load(source_slot, name=f"{prefix}_elem")
        codegen.builder.store(
            codegen.utils.cast_for_param(copy_out(codegen, held, element_type),
                                         element_llvm_type),
            slot)

    emit_container_walk(codegen, dest_data, count, clone_one, prefix=f"{prefix}_clone")


def _emit_memcpy(codegen: 'LLVMCodegen', dest: ir.Value, source: ir.Value, count: ir.Value,
                 element_llvm_type: ir.Type) -> None:
    """`count` elements, in one call. The stride is the ABI ALLOC size, never the data size."""
    from sushi_lang.backend.expressions import memory

    total_bytes = memory.emit_byte_size(codegen, count, element_llvm_type, name="copy_bytes")
    memory.emit_memcpy_bytes(codegen, dest, source, total_bytes)


def clamp_range(codegen: 'LLVMCodegen', start: ir.Value, extent: ir.Value,
                source_len: ir.Value, *, extent_is_end: bool) -> tuple:
    """A requested range, narrowed to what the source can answer.

    ONE rule for every spelling. `.s` and `.extend_range` differ only in whether `extent` is
    an exclusive END index or a LENGTH, which is the one branch here:

        start = clamp(start, 0, len)
        count = clamp(end, start, len) - start     # an end index
        count = clamp(count, 0, len - start)       # a length

    Checked against the string twins, which this must agree with: `"hello".s(-2, 3)` is
    "hel", `.s(9, 12)` and `.s(3, 1)` are both "", `.ss(2, 99)` is "llo" and `.ss(2, -2)`
    is "". The start is clamped FIRST, which is what makes `.s(-2, 3)` three elements and
    not five.

    Nothing traps. A negative count cannot reach `emit_container_walk`, so the unsigned
    compare there is safe by construction.
    """
    b = codegen.builder
    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)

    start = _clamp(codegen, start, zero, source_len, "copy_start")

    if extent_is_end:
        end = _clamp(codegen, extent, start, source_len, "copy_end")
        return start, b.sub(end, start, name="copy_count")

    room = b.sub(source_len, start, name="copy_room")
    return start, _clamp(codegen, extent, zero, room, "copy_count")


def _clamp(codegen: 'LLVMCodegen', value: ir.Value, low: ir.Value, high: ir.Value,
           name: str) -> ir.Value:
    """`min(max(value, low), high)`, signed. Two compares and two selects."""
    b = codegen.builder
    at_least = b.select(b.icmp_signed("<", value, low), low, value, name=f"{name}_low")
    return b.select(b.icmp_signed(">", at_least, high), high, at_least, name=name)


_I32_MAX = 0x7FFFFFFF


def emit_dynamic_array_extend_back(codegen: 'LLVMCodegen', array_address: ir.Value,
                                   element_llvm_type: ir.Type, dist: ir.Value,
                                   count: ir.Value, element_type: 'Type') -> ir.Value:
    """`extend_back(dist, count)`: append `count` elements read `dist` slots back.

    Element `k` is a copy of `data[len - dist + k]`, read AFTER the earlier appends, so a
    count larger than the distance repeats the last `dist` elements (the LZ77 rule). A
    `dist` outside `1..len` or a `count` below 1 appends nothing, the no-op rule of the
    bulk-copy family. The array grows once, and the copy reads the data pointer that the
    growth answers. A length past the i32 range is the allocation failure RE2021.

    The source is the receiver itself, so this is not a bulk write from a borrowed source.
    An owning element takes its own `copy_out` per slot in a forward walk; a plain one is a
    `memcpy` when the two ranges are apart (`dist >= count`) and a forward walk when they
    overlap, because a `memcpy` or a `memmove` would not repeat the pattern.
    """
    from sushi_lang.backend.destructors import needs_cleanup
    from sushi_lang.backend.expressions import memory

    b = require_builder(codegen)
    i32 = codegen.types.i32
    one = ir.Constant(i32, 1)

    len_ptr = gep_utils.gep_dynamic_array_len(codegen, array_address)
    cap_ptr = gep_utils.gep_dynamic_array_cap(codegen, array_address)
    data_ptr_ptr = gep_utils.gep_dynamic_array_data(codegen, array_address)
    length = b.load(len_ptr, name="back_len")

    dist_in_range = b.and_(b.icmp_signed(">=", dist, one), b.icmp_signed("<=", dist, length),
                           name="back_dist_ok")
    appends = b.and_(dist_in_range, b.icmp_signed(">=", count, one), name="back_appends")

    with b.if_then(appends):
        room = b.sub(ir.Constant(i32, _I32_MAX), length, name="back_room")
        with b.if_then(b.icmp_signed(">", count, room, name="back_too_long")):
            codegen.runtime.errors.emit_runtime_error("RE2021")
            b.unreachable()

        needed = b.add(length, count, name="back_needed")
        data = memory.emit_grow_to_fit(
            codegen, data_ptr=b.load(data_ptr_ptr, name="back_data"),
            data_ptr_ptr=data_ptr_ptr, cap_ptr=cap_ptr,
            current_cap=b.load(cap_ptr, name="back_cap"), count=needed,
            element_llvm_type=element_llvm_type, policy=memory.GrowPolicy.AT_LEAST)
        dest = gep_utils.gep_array_element(codegen, data, length, "back_dest")
        start = b.sub(length, dist, name="back_start")

        if needs_cleanup(codegen, element_type):
            emit_range_copy(codegen, dest, data, start, count, element_type,
                            element_llvm_type, prefix="back")
        else:
            with b.if_else(b.icmp_signed(">=", dist, count, name="back_apart")) as (apart,
                                                                                    overlap):
                with apart:
                    emit_range_copy(codegen, dest, data, start, count, element_type,
                                    element_llvm_type, prefix="back")
                with overlap:
                    _emit_forward_run(codegen, dest, data, start, count)

        b.store(needed, len_ptr)

    return ir.Constant(i32, 0)


def _emit_forward_run(codegen: 'LLVMCodegen', dest: ir.Value, data: ir.Value,
                      start: ir.Value, count: ir.Value) -> None:
    """`dest[k] = data[start + k]` for k upward: each read sees the earlier writes."""
    b = require_builder(codegen)
    source_base = gep_utils.gep_array_element(codegen, data, start, "back_run_src")

    def copy_one(slot: ir.Value, index: ir.Value) -> None:
        source_slot = gep_utils.gep_array_element(codegen, source_base, index)
        b.store(b.load(source_slot, name="back_run_elem"), slot)

    emit_container_walk(codegen, dest, count, copy_one, prefix="back_run")
