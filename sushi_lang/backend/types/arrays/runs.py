"""Filling an array from elements that fill more than one slot (#446, #478).

Two do: `value; count` is read once and stored `count` times, and `a..b` yields a sequence.
The rule that shapes this module: **a long run is a loop, never a line of stores.** A 32768
entry table written as 32768 stores grows the IR and the compile time with N, which is the
cost the syntax exists to remove.

**A readable count never pays for the run-time mechanism.** That is a rule and not a hope
about the optimizer: llvmlite does not fold, so `builder.add` of two constants emits
`add i32 3, 4` into the module, and at `--opt none` there is no second chance. Three tiers:

| what the compiler knows | what it emits |
|---|---|
| readable, `count <= UNROLL_LIMIT` | one `store` of a literal per slot. No arithmetic, no loop |
| readable, `count > UNROLL_LIMIT` | one walk with a CONSTANT trip count |
| not readable | the same walk, with computed `first`, `step` and `count` |

`tests/unit/test_range_fill_tiers.py` is the gate on that table.

A CURSOR, not a constant start: a run-time count makes every later start a run-time value,
and the cursor is shorter than the constant arithmetic it replaces. It is also why a
run-time element may sit anywhere in a literal -- nothing depends on a compile-time
position, and LLVM folds the adds when every count is constant.

The semantic side of a run lives in `semantics/array_runs.py`, and this module reads its
runs through the same seam so the two never disagree about a count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence

from llvmlite import ir

from sushi_lang.backend import gep_utils
from sushi_lang.backend.generics.container_walk import emit_container_walk
from sushi_lang.semantics import array_runs

if TYPE_CHECKING:
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.backend.ranges import EmittedRange
    from sushi_lang.semantics.ast import ArrayElement
    from sushi_lang.semantics.ranges import RangePlan
    from sushi_lang.semantics.typesys import Type

# Below this many slots a run costs less as plain stores than as a counted loop.
UNROLL_LIMIT = 8


@dataclass(frozen=True)
class EmittedRun:
    """One run: how many slots it fills, and how to make the value for each slot.

    Exactly one of `value`, `plan` and `span` says which kind it is: a repeated value, a
    range the compiler could read, and a range it could not.
    """
    count: ir.Value                                  # a Constant when the count was readable
    value: Optional[ir.Value] = None                 # a repeated value
    plan: Optional['RangePlan'] = None               # a readable range
    span: Optional['EmittedRange'] = None            # a run-time range
    element_type: Optional['Type'] = None            # for the per-slot copy of an owning value


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

    A RANGE emits nothing when the compiler could read its bounds -- the plan carries the
    values. Only an unreadable one calls `emit_range`, which is what keeps a readable range
    off the run-time path (see the tier table above).

    A repeated OWNING value emits once and is copied per slot in `fill_runs` (Ruling 7,
    #478). It is a BORROW, so the source stays usable: a run has no single position, and
    consuming means one value reaching one position.
    """
    from sushi_lang.backend.ownership import ConsumingUse, consume
    from sushi_lang.backend.expressions.calls.utils import emit_borrowed_arg
    from sushi_lang.backend.ranges import emit_range
    from sushi_lang.backend.types.arrays.utils import alias_element_type
    from sushi_lang.semantics import typesys

    runs = read_runs(codegen, elements)
    if runs is None:
        runs = []

    i32 = codegen.types.i32
    emitted: List[EmittedRun] = []
    for run in runs:
        if run.is_range:
            if run.plan is not None:
                emitted.append(EmittedRun(count=ir.Constant(i32, run.plan.count),
                                          plan=run.plan))
                continue
            span = emit_range(codegen, run.value)
            emitted.append(EmittedRun(count=span.count, span=span))
            continue

        ety = element_type if element_type is not None else alias_element_type(codegen, run.value)

        if run.count is None:
            count = codegen.utils.as_i32(codegen.expressions.emit_expr(run.count_expr))
            count = _reject_negative_count(codegen, count)
        else:
            count = ir.Constant(i32, run.count)

        if run.is_repeat:
            # Ruling 7: a REPEATED value borrows, whatever its type. It has N slots to
            # satisfy and one value, so there is no single position to consume into -- the
            # rule `.fill()` already follows (#479). `emit_borrowed_arg` is the built-in
            # borrow seam, and it gives an owning TEMPORARY an owner (#475).
            value = emit_borrowed_arg(codegen, run.value, ety)
            owning = ety is not None and typesys.owns_heap(ety)
        else:
            # One value, one slot, one position. This still CONSUMES.
            value = consume(codegen, run.value, codegen.expressions.emit_expr(run.value),
                            ety, ConsumingUse.ARRAY_ELEMENT)
            owning = False

        emitted.append(EmittedRun(count=count, value=value,
                                  element_type=ety if owning else None))
    return emitted


def _reject_negative_count(codegen: 'LLVMCodegen', count: ir.Value) -> ir.Value:
    """Trap RE2024 on a negative run-time count (Ruling 6, #478).

    `emit_container_walk` compares with an unsigned predicate, so -1 reads as four billion
    and the fill runs off the end of the buffer. That is a memory-safety hole, so it cannot
    be left to fall out, and a clamp to zero would turn a wrong program into a silently
    empty one. Emitted only for a count that is not already a constant.
    """
    if isinstance(count, ir.Constant):
        return count

    b = codegen.builder
    ok = b.icmp_signed(">=", count, ir.Constant(codegen.types.i32, 0), name="count_not_negative")
    ok_block = b.append_basic_block(name="count_ok")
    fail_block = b.append_basic_block(name="count_negative")
    b.cbranch(ok, ok_block, fail_block)

    b.position_at_end(fail_block)
    codegen.runtime.errors.emit_runtime_error_with_values("RE2024", count)
    b.unreachable()

    b.position_at_end(ok_block)
    return count


def single_runs(values: Sequence[ir.Value]) -> List[EmittedRun]:
    """Already-emitted values as runs of one slot each.

    For a caller that never had a literal to read -- the variadic collector builds its
    `T[]` from the trailing arguments -- so the array builders take one shape only.
    """
    from llvmlite import ir as _ir
    one = _ir.Constant(_ir.IntType(32), 1)
    return [EmittedRun(count=one, value=value) for value in values]


def element_llvm_type(codegen: 'LLVMCodegen', emitted: Sequence[EmittedRun]) -> ir.Type:
    """The LLVM type of a slot, read from the runs rather than guessed from a value.

    A range run has no emitted value to read a type off, and it does not need one: a range
    yields i32 (Ruling 4, #478). Every run in one literal fills the same slot type -- CE2013
    saw to that -- so the first run that can answer, answers.
    """
    for run in emitted:
        if run.value is not None:
            return run.value.type
        return codegen.types.i32
    raise ValueError("no runs to read an element type from")


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
    """One constant per slot, or None when any run must be stored at run time.

    A readable RANGE qualifies as constant: the plan carries its values, so a literal of
    ranges lands in an initializer rather than in a fill loop.
    """
    out: List[ir.Value] = []
    for run in emitted:
        if run.plan is not None:
            out.extend(ir.Constant(element_llvm_type, value) for value in run.plan.values())
            continue
        if run.value is None or not isinstance(run.value, ir.Constant):
            return None
        if not isinstance(run.count, ir.Constant):
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

        if run.plan is not None and run.plan.count <= UNROLL_LIMIT:
            # Tier 1: literals. Byte for byte what a spelled-out literal emits.
            for offset, value in enumerate(run.plan.values()):
                slot = gep_utils.gep_array_element(codegen, base, ir.Constant(i32, offset))
                codegen.builder.store(
                    codegen.utils.cast_for_param(ir.Constant(i32, value), element_llvm_type),
                    slot)
        elif run.plan is not None:
            # Tier 2: a walk with a CONSTANT trip count. `first` and `step` are constants,
            # so the body is one `mul` and one `add` on the loop index.
            _walk_range(codegen, base, run.count, ir.Constant(i32, run.plan.first),
                        ir.Constant(i32, run.plan.step), element_llvm_type, number)
        elif run.span is not None:
            # Tier 3: the same walk, with everything computed.
            _walk_range(codegen, base, run.count, run.span.first, run.span.step,
                        element_llvm_type, number)
        else:
            _fill_repeated(codegen, base, run, element_llvm_type, number)

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
    """One value into every slot the run fills.

    An OWNING element takes its own `copy_out` per slot (Ruling 7, #478) -- the sanctioned
    deep-clone seam, the shape `.fill()` uses. There is no destroy step: a literal writes
    into a FRESH buffer, so no slot holds anything yet.
    """
    from sushi_lang.backend.ownership import copy_out

    value = codegen.utils.cast_for_param(run.value, element_llvm_type)

    def store_one(slot: ir.Value, _index: ir.Value) -> None:
        held = value
        if run.element_type is not None:
            held = codegen.utils.cast_for_param(
                copy_out(codegen, run.value, run.element_type), element_llvm_type)
        codegen.builder.store(held, slot)

    if isinstance(run.count, ir.Constant) and run.count.constant <= UNROLL_LIMIT:
        i32 = codegen.types.i32
        for offset in range(run.count.constant):
            slot = gep_utils.gep_array_element(codegen, base, ir.Constant(i32, offset))
            store_one(slot, ir.Constant(i32, offset))
        return

    emit_container_walk(codegen, base, run.count, store_one, prefix=f"run{number}_fill")


def fill_fixed_slot(codegen: 'LLVMCodegen', slot: ir.Value,
                    emitted: Sequence[EmittedRun], element_llvm_type: ir.Type) -> None:
    """The same fill against a fixed array's alloca, which needs the leading zero index."""
    zero = ir.Constant(codegen.types.i32, 0)
    data_ptr = codegen.builder.gep(slot, [zero, zero], name="array_data")
    fill_runs(codegen, data_ptr, emitted, element_llvm_type)
