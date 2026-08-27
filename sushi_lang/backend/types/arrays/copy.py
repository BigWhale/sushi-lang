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

Two traps, and neither is new. A negative count is **RE2024**, the code a negative repeat
count takes: the walk compares with an unsigned predicate, so one hazard from one cause
carries one code. A `start + count` past the source is **RE2020**, which is already the
out-of-range read.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.semantics import typesys

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type


def emit_range_copy(codegen: 'LLVMCodegen', dest_data: ir.Value, source_data: ir.Value,
                    start: ir.Value, count: ir.Value, element_type: Optional['Type'],
                    element_llvm_type: ir.Type, prefix: str = "copy") -> None:
    """Copy `source_data[start .. start + count)` into `dest_data[0 .. count)`.

    The caller has already made room and checked the bounds. This is the copy alone.
    """
    from sushi_lang.backend.ownership import copy_out

    source_base = gep_utils.gep_array_element(codegen, source_data, start, f"{prefix}_src")

    if element_type is None or not typesys.owns_heap(element_type):
        _emit_memcpy(codegen, dest_data, source_base, count, element_llvm_type)
        return

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


def reject_bad_range(codegen: 'LLVMCodegen', start: ir.Value, count: ir.Value,
                     source_len: ir.Value) -> None:
    """Trap a range the source cannot answer.

    RE2024 for a negative `start` or `count`, and RE2020 for a `start + count` past the end.
    Both are checked before anything is allocated, so a trap leaves nothing behind.
    """
    b = codegen.builder
    i32 = codegen.types.i32
    zero = ir.Constant(i32, 0)

    start_ok = b.icmp_signed(">=", start, zero, name="copy_start_not_negative")
    count_ok = b.icmp_signed(">=", count, zero, name="count_not_negative")
    both_ok = b.and_(start_ok, count_ok, name="copy_counts_ok")

    ok_block = b.append_basic_block(name="copy_counts_ok")
    fail_block = b.append_basic_block(name="copy_count_negative")
    b.cbranch(both_ok, ok_block, fail_block)

    b.position_at_end(fail_block)
    codegen.runtime.errors.emit_runtime_error_with_values("RE2024", count)
    b.unreachable()

    b.position_at_end(ok_block)
    end = b.add(start, count, name="copy_end")
    in_bounds = b.icmp_signed("<=", end, source_len, name="copy_in_bounds")

    range_ok = b.append_basic_block(name="copy_range_ok")
    range_fail = b.append_basic_block(name="copy_range_fail")
    b.cbranch(in_bounds, range_ok, range_fail)

    b.position_at_end(range_fail)
    codegen.runtime.errors.emit_runtime_error_with_values("RE2020", end, source_len)
    b.unreachable()

    b.position_at_end(range_ok)
