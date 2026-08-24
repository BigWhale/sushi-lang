"""Filling an array from repeated elements (#446, Ruling 2).

`value; count` is read once and stored `count` times. The rule that shapes this module:
**a long run is a loop, never a line of stores.** A 32768-entry table written as 32768
stores grows the IR and the compile time with N, which is the cost the syntax exists to
remove.

The semantic side of a run lives in `semantics/array_runs.py`, and this module reads its
runs through the same seam so the two never disagree about a count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Sequence

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.semantics import array_runs

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.semantics.ast import ArrayElement

# Below this many slots a run costs less as plain stores than as a counted loop.
UNROLL_LIMIT = 8


@dataclass(frozen=True)
class EmittedRun:
    """One emitted value and the number of slots it fills, from `start`."""
    value: ir.Value
    count: int
    start: int


def read_runs(codegen: 'LLVMCodegen', elements: Sequence['ArrayElement']):
    """The runs of a literal, or None when a count is not readable.

    Silent: the typecheck pass already reported CE2017 for anything wrong here.
    """
    from sushi_lang.internals.report import Reporter

    return array_runs.read_runs(
        elements,
        array_runs.const_int_reader(codegen.const_table, codegen.ast_constants),
        Reporter())


def emit_runs(codegen: 'LLVMCodegen', elements: Sequence['ArrayElement'],
              element_type) -> List[EmittedRun]:
    """Emit ONE value per run, consumed as an array element.

    A run of more than one slot is a plain type (CE2018), so one emitted value is copied
    into every slot it fills and no deep copy is ever needed.
    """
    from sushi_lang.backend.ownership import ConsumingUse, consume
    from sushi_lang.backend.types.arrays.utils import alias_element_type

    runs = read_runs(codegen, elements)
    if runs is None:
        runs = []

    emitted: List[EmittedRun] = []
    for run in runs:
        value = codegen.expressions.emit_expr(run.value)
        ety = element_type if element_type is not None else alias_element_type(codegen, run.value)
        value = consume(codegen, run.value, value, ety, ConsumingUse.ARRAY_ELEMENT)
        emitted.append(EmittedRun(value=value, count=run.count, start=run.start))
    return emitted


def single_runs(values: Sequence[ir.Value]) -> List[EmittedRun]:
    """Already-emitted values as runs of one slot each.

    For a caller that never had a literal to read -- the variadic collector builds its
    `T[]` from the trailing arguments -- so the array builders take one shape only.
    """
    return [EmittedRun(value=value, count=1, start=index)
            for index, value in enumerate(values)]


def total_elements(emitted: Sequence[EmittedRun]) -> int:
    """How many slots the runs fill together."""
    return sum(run.count for run in emitted)


def fill_runs(codegen: 'LLVMCodegen', data_ptr: ir.Value,
              emitted: Sequence[EmittedRun], element_llvm_type: ir.Type) -> None:
    """Store every run into `data_ptr[start .. start + count)`."""
    i32 = codegen.types.i32

    for number, run in enumerate(emitted):
        value = codegen.utils.cast_for_param(run.value, element_llvm_type)

        if run.count <= UNROLL_LIMIT:
            for offset in range(run.count):
                index = ir.Constant(i32, run.start + offset)
                slot = gep_utils.gep_array_element(codegen, data_ptr, index)
                codegen.builder.store(value, slot)
            continue

        def store_one(slot: ir.Value, _index: ir.Value, held: ir.Value = value) -> None:
            codegen.builder.store(held, slot)

        base = gep_utils.gep_array_element(codegen, data_ptr, ir.Constant(i32, run.start),
                                           f"run{number}_base")
        emit_container_walk(codegen, base, ir.Constant(i32, run.count), store_one,
                            prefix=f"run{number}_fill")


def fill_fixed_slot(codegen: 'LLVMCodegen', slot: ir.Value,
                    emitted: Sequence[EmittedRun], element_llvm_type: ir.Type) -> None:
    """The same fill against a fixed array's alloca, which needs the leading zero index."""
    zero = ir.Constant(codegen.types.i32, 0)
    data_ptr = codegen.builder.gep(slot, [zero, zero], name="array_data")
    fill_runs(codegen, data_ptr, emitted, element_llvm_type)
