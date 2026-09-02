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

    element_size = memory.get_element_size_constant(codegen, element_llvm_type)
    total_bytes = codegen.builder.mul(count, element_size, name="copy_bytes")
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
