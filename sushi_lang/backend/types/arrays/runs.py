"""An array value from the runs of an `Aggregate(AggArray)` (docs/design/ir.md 8.7; #446, #478).

The SLIR emitter gives the value of each part, and this module fills the slots. A run is one
value, a repeated value with its count, or a sequence (`first + step * k`) with its count.
The rule that shapes this module: **a long run is a loop, never a line of stores.** A 32768
entry table written as 32768 stores grows the IR and the compile time with N, which is the
cost the syntax exists to remove.

**A readable count never pays for the run-time mechanism.** That is a rule and not a hope
about the optimizer: llvmlite does not fold, so `builder.add` of two constants emits
`add i32 3, 4` into the module, and at `--opt none` there is no second chance. SLIR lowering
gives a constant count, and the constant first value of a range with two constant bounds, as
a constant. Three tiers:

| what the compiler knows | what it emits |
|---|---|
| readable, `count <= UNROLL_LIMIT` | one `store` of a literal per slot. No arithmetic, no loop |
| readable, `count > UNROLL_LIMIT` | one walk with a CONSTANT trip count |
| not readable | the same walk, with computed `first` and `count` |

A repeated value that would take a walk is one `llvm.memset` when it is a constant and
every byte of it is the same byte (`0`, `-1`, `0.0`, `7` as a `u8`). A `T[N]` whose every
part is a constant is one constant. A repeated value of an owning element is a borrow: each
slot takes its own `copy_out`, the deep-clone seam (Ruling 7, #478).

A CURSOR, not a constant start: a run-time count makes every later start a run-time value,
and the cursor is shorter than the constant arithmetic it replaces. A run-time count below
zero fills nothing: it is read as zero (Ruling 2, #478).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.backend.memory.allocas import entry_alloca
from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.slir import rvalues as r
from sushi_lang.semantics.typesys import ArrayType, DynamicArrayType

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.typesys import Type

# Below this many slots a run costs less as plain stores than as a counted loop.
UNROLL_LIMIT = 8


@dataclass(frozen=True)
class EmittedRun:
    """One run: how many slots it fills, and the value of each slot. A repeated value has
    `value`; a sequence has `first` and `step`, and slot k holds `first + step * k`."""
    count: ir.Value                                  # a Constant when the count is readable
    value: Optional[ir.Value] = None
    first: Optional[ir.Value] = None
    step: int = 1
    owning: Optional['Type'] = None                  # the element type of an owning value


def emit_array_value(codegen: 'LLVMCodegen', ty: ArrayType | DynamicArrayType,
                     runs: Sequence[r.ArrayRun], parts: Sequence[ir.Value]) -> ir.Value:
    """The value of an array of the runs `runs`, whose parts have the values `parts`: a
    `T[N]` by value, or the `{len, cap, data}` descriptor of a fresh `T[]`."""
    from sushi_lang.backend.types.arrays.utils import create_dynamic_array_from_elements

    from sushi_lang.backend.destructors import needs_cleanup

    element = codegen.types.ll_type(ty.base_type)
    owning = ty.base_type if needs_cleanup(codegen, ty.base_type) else None
    emitted = _emitted_runs(codegen, runs, parts, owning)
    if isinstance(ty, DynamicArrayType):
        return create_dynamic_array_from_elements(codegen, element, emitted)
    array_type = ir.ArrayType(element, ty.size)
    expanded = constant_values(emitted, element)
    if expanded is not None:
        return ir.Constant(array_type, expanded)
    slot = entry_alloca(codegen.builder, array_type, "array_literal")
    zero = ir.Constant(codegen.types.i32, 0)
    fill_runs(codegen, codegen.builder.gep(slot, [zero, zero], name="array_data"), emitted,
              element)
    return codegen.builder.load(slot)


def _emitted_runs(codegen: 'LLVMCodegen', runs: Sequence[r.ArrayRun],
                  parts: Sequence[ir.Value], owning: Optional['Type']) -> List[EmittedRun]:
    """Each run with its values, in order; a run-time count is clamped to zero. A repeated
    value of the owning element type `owning` is copied into each slot."""
    emitted: List[EmittedRun] = []
    at = 0
    for run in runs:
        if isinstance(run, r.RunOne):
            emitted.append(EmittedRun(count=ir.Constant(codegen.types.i32, 1), value=parts[at]))
        elif isinstance(run, r.RunRepeat):
            emitted.append(EmittedRun(count=_clamp_count(codegen, parts[at + 1]),
                                      value=parts[at], owning=owning))
        else:
            emitted.append(EmittedRun(count=_clamp_count(codegen, parts[at + 1]),
                                      first=parts[at], step=run.step))
        at += r.run_width(run)
    return emitted


def _clamp_count(codegen: 'LLVMCodegen', count: ir.Value) -> ir.Value:
    """A run-time count, narrowed to zero when it is negative.

    `emit_container_walk` compares with an unsigned predicate, so -1 would read as four
    billion and the fill would run off the end of the buffer. The clamp removes that by
    construction. A constant count is kept: typecheck refused one below one.
    """
    if isinstance(count, ir.Constant):
        return count

    b = codegen.builder
    zero = ir.Constant(codegen.types.i32, 0)
    return b.select(b.icmp_signed("<", count, zero), zero, count, name="run_count")


def readable_total(emitted: Sequence[EmittedRun]) -> Optional[int]:
    """How many slots the runs fill, or None when one count is only known at run time."""
    total = 0
    for run in emitted:
        if not isinstance(run.count, ir.Constant):
            return None
        total += run.count.constant
    return total


def constant_values(emitted: Sequence[EmittedRun],
                    element_llvm_type: ir.Type) -> Optional[List[ir.Value]]:
    """One constant per slot, or None when any run must be stored at run time."""
    out: List[ir.Value] = []
    for run in emitted:
        if not isinstance(run.count, ir.Constant):
            return None
        if run.first is not None:
            if not isinstance(run.first, ir.Constant):
                return None
            out.extend(ir.Constant(element_llvm_type, run.first.constant + run.step * index)
                       for index in range(run.count.constant))
            continue
        if not isinstance(run.value, ir.Constant) or run.owning is not None:
            return None
        out.extend([run.value] * run.count.constant)
    return out


def emit_total_length(codegen: 'LLVMCodegen', emitted: Sequence[EmittedRun]) -> ir.Value:
    """The length as a value, summing the counts. Constant when every count is."""
    i32 = codegen.types.i32
    total: ir.Value = ir.Constant(i32, 0)
    for run in emitted:
        if isinstance(total, ir.Constant) and isinstance(run.count, ir.Constant):
            total = ir.Constant(i32, total.constant + run.count.constant)
            continue
        total = codegen.builder.add(total, run.count, name="run_total")
    return total


def fill_runs(codegen: 'LLVMCodegen', data_ptr: ir.Value,
              emitted: Sequence[EmittedRun], element_llvm_type: ir.Type) -> None:
    """Store every run into the slots that follow the cursor."""
    i32 = codegen.types.i32
    cursor: ir.Value = ir.Constant(i32, 0)

    for number, run in enumerate(emitted):
        base = gep_utils.gep_array_element(codegen, data_ptr, cursor, f"run{number}_base")

        if run.first is None:
            _fill_repeated(codegen, base, run, element_llvm_type, number)
        elif (isinstance(run.first, ir.Constant) and isinstance(run.count, ir.Constant)
              and run.count.constant <= UNROLL_LIMIT):
            # Tier 1: literals. Byte for byte what a spelled-out literal emits.
            for offset in range(run.count.constant):
                slot = gep_utils.gep_array_element(codegen, base, ir.Constant(i32, offset))
                value = ir.Constant(i32, run.first.constant + run.step * offset)
                codegen.builder.store(codegen.utils.cast_for_param(value, element_llvm_type),
                                      slot)
        else:
            # Tier 2 and 3: a walk. With a constant first value and count, the body is one
            # `mul` and one `add` on the loop index.
            _walk_range(codegen, base, run.count, run.first, ir.Constant(i32, run.step),
                        element_llvm_type, number)

        cursor = codegen.builder.add(cursor, run.count, name=f"cursor{number}") \
            if not (isinstance(cursor, ir.Constant) and isinstance(run.count, ir.Constant)) \
            else ir.Constant(i32, cursor.constant + run.count.constant)


def _walk_range(codegen: 'LLVMCodegen', base: ir.Value, count: ir.Value, first: ir.Value,
                step: ir.Value, element_llvm_type: ir.Type, number: int) -> None:
    """`slot i holds first + step * i`, over a counted walk."""
    def store_one(slot: ir.Value, index: ir.Value) -> None:
        offset = codegen.builder.mul(step, index, name=f"run{number}_offset")
        value = codegen.builder.add(first, offset, name=f"run{number}_value")
        codegen.builder.store(codegen.utils.cast_for_param(value, element_llvm_type), slot)

    emit_container_walk(codegen, base, count, store_one, prefix=f"run{number}_range")


def _fill_repeated(codegen: 'LLVMCodegen', base: ir.Value, run: EmittedRun,
                   element_llvm_type: ir.Type, number: int) -> None:
    """One value into every slot the run fills: stores, one `memset`, or a walk. An owning
    value takes its own `copy_out` per slot; the buffer is fresh, so no slot holds a value
    to destroy first."""
    from sushi_lang.backend.ownership import copy_out

    if run.value is None:
        raise_internal_error("CE0000", detail="a repeated run of an array has no value")
    value = codegen.utils.cast_for_param(run.value, element_llvm_type)
    owning = run.owning

    def store_one(slot: ir.Value, _index: ir.Value) -> None:
        held = value if owning is None else codegen.utils.cast_for_param(
            copy_out(codegen, run.value, owning), element_llvm_type)
        codegen.builder.store(held, slot)

    if isinstance(run.count, ir.Constant) and run.count.constant <= UNROLL_LIMIT:
        i32 = codegen.types.i32
        for offset in range(run.count.constant):
            slot = gep_utils.gep_array_element(codegen, base, ir.Constant(i32, offset))
            store_one(slot, ir.Constant(i32, offset))
        return

    byte = None if owning is not None else _repeated_byte(value)
    if byte is not None:
        from sushi_lang.backend.expressions.memory import emit_byte_size, emit_memset_bytes
        emit_memset_bytes(codegen, base, byte,
                          emit_byte_size(codegen, run.count, element_llvm_type,
                                         name=f"run{number}_bytes"))
        return

    emit_container_walk(codegen, base, run.count, store_one, prefix=f"run{number}_fill")


def _repeated_byte(value: ir.Value) -> Optional[int]:
    """The one byte that every byte of `value` holds, or None.

    Only a constant integer or float answers. Then a run of it is one `llvm.memset`.
    """
    if not isinstance(value, ir.Constant):
        return None
    if isinstance(value.type, ir.IntType) and isinstance(value.constant, int):
        if value.type.width % 8 != 0:
            return None
        raw = (value.constant & ((1 << value.type.width) - 1)).to_bytes(
            value.type.width // 8, "little")
    elif isinstance(value.type, (ir.FloatType, ir.DoubleType)) \
            and isinstance(value.constant, (int, float)):
        raw = struct.pack("<f" if isinstance(value.type, ir.FloatType) else "<d",
                          value.constant)
    else:
        return None
    return raw[0] if raw == raw[:1] * len(raw) else None
