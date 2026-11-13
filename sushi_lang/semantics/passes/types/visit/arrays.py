"""The array arms of the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.4).

An `ArrayInit` checks each run at the element type of its position, when the position
gives an array type: a value, a repeat (the value, then the count, ruling R2) and a range,
whose slot is an i32. A count is an index position, of any integer type. The one reader of the runs
(`array_runs.plan_runs`) says when a count or a range fills no slot (CE2017, CE2019,
CE2020, CE2125). An element whose type is not the type of the first is CE2013; when the
rows of a nested literal do not agree, each row is first measured against the row type of
its position, and a short row is CE2011. A fixed literal answers `T[N]`, where `T` is the
type of its first element and `N` the count of its slots, which must be readable (CE2017,
CE2019). A dynamic literal answers `T[]` when its elements agree. An empty `from([])` and a
`new()` answer the `T[]` of their position (#544); a position with no `T[]` is CE2111,
which names the written form.

An `Index` reads an element of a `T[N]` or a `T[]`, or a `u8` of a `string`. The index is
an index position, of any integer type (CE2121). A base of another type is CE2114. An index of a fixed array that
the compiler can read is in range (CE2056, CE2012). An index answers its element type also
after an error of the index. A `Slice` is a `T[]` view of either array kind: its bounds
are index positions, and readable bounds go up (CE2125). A string has no slice (CE2122), and
a base of another type is CE2114. A base of a type that the pass has no rule for (a
type pack, a type whose name the unit lost) is an internal error.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics import array_runs
from sushi_lang.semantics.const_eval import is_integer_constant
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arrays import (
    ARRAY_INDEX, REPEAT_COUNT, STRING_INDEX, check_index_operand, reject_index_out_of_range,
    reject_non_index)
from sushi_lang.semantics.passes.types.visit.ranges import check_range_run, check_slice_bounds
from sushi_lang.semantics.ranges import RangePlan, plan_range, reject_a_range_that_goes_down
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, DynamicArrayType, Type, UnknownType, deref_type)

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


# -- a construction ----------------------------------------------------------------------


def check_array_init(validator: TypeValidator, node: e.ArrayInit,
                     expected: Optional[Type]) -> Optional[Type]:
    """The array that a literal, a `from(...)` or a `new()` builds; none after an error."""
    position = deref_type(expected)
    if not node.runs:
        return _empty(validator, node, position)
    element = (position.base_type if isinstance(position, (ArrayType, DynamicArrayType))
               else None)
    slots = [_check_run(validator, run, element) for run in node.runs]
    runs = _plan(validator, node, validator.reporter)
    if _short_rows(validator, node, slots, element):
        return None
    _reject_mixed_elements(validator, node, slots)
    first = slots[0]
    if first is None or runs is None:
        return None
    if isinstance(node.kind, e.Dynamic):
        return DynamicArrayType(first) if all(slot == first for slot in slots) else None
    size = array_runs.require_readable_length(runs, validator.reporter)
    return None if size is None else ArrayType(first, size)


def _empty(validator: TypeValidator, node: e.ArrayInit,
           position: Optional[Type]) -> Optional[Type]:
    """`from([])` and `new()` take the `T[]` of their position (#544)."""
    if isinstance(node.kind, e.Dynamic) and isinstance(position, DynamicArrayType):
        return position
    if not isinstance(node.kind, e.Dynamic):
        visit.unhandled(validator, node)
    form = "new()" if node.kind.new else "from([])"
    er.emit(validator.reporter, er.ERR.CE2111, node.loc, form=form)
    return None


def _check_run(validator: TypeValidator, run: e.ArrayRun,
               element: Optional[Type]) -> Optional[Type]:
    """Check one run, and answer the type that it puts in a slot: the type of its value,
    or i32 for a range."""
    if isinstance(run, e.RangeRun):
        check_range_run(validator, run.range)
        return BuiltinType.I32
    if isinstance(run.value, e.Range):
        check_range_run(validator, run.value)
        slot: Optional[Type] = BuiltinType.I32
    else:
        slot = visit.check_expr(validator, run.value, element)
    if isinstance(run, e.Repeat):
        reject_non_index(validator, run.count, check_index_operand(validator, run.count),
                         position=REPEAT_COUNT)
    return slot


def _value_of(run: e.ArrayRun) -> e.ShirExpr:
    return run.range if isinstance(run, e.RangeRun) else run.value


def _elements(node: e.ArrayInit) -> list[array_runs.Element]:
    """The runs of `node` as the reader of the runs reads them."""
    elements = []
    for run in node.runs:
        value = _value_of(run)
        count = run.count if isinstance(run, e.Repeat) else None
        loc = value.loc if count is None else _join(value.loc, count.loc)
        elements.append(array_runs.Element(value, count, isinstance(value, e.Range), loc))
    return elements


def _join(first: Span, last: Span) -> Span:
    return Span(first.line, first.col, last.end_line, last.end_col)


def _plan(validator: TypeValidator, node: e.ArrayInit,
          reporter: Reporter) -> Optional[list[array_runs.Run]]:
    """The runs of `node`, with each count and each range read by the fold."""
    folder = validator.body_folder()

    def count(value: e.ShirExpr) -> Optional[int]:
        read = folder.scalar(value, validator.body_answers().type_of(value.shir_id))
        return int(read.value) if read is not None and is_integer_constant(read) else None

    def plan(value: e.ShirExpr) -> Optional[RangePlan]:
        read = folder.read(value, None)
        return read if isinstance(read, RangePlan) else None

    return array_runs.plan_runs(_elements(node), count, plan, reporter)


def reject_wrong_size(validator: TypeValidator, node: e.ArrayInit, declared: ArrayType,
                      span: Span) -> bool:
    """CE2011 when the slots of a fixed literal are not the size of the declared `T[N]`. A
    note gives the slots that each run fills, when a run fills more than one. Answers
    whether the literal is refused: a run that fills no slot, and a count that the
    compiler cannot read, are refused too, and the arm of the literal reports them."""
    runs = _plan(validator, node, Reporter())
    if runs is None:
        return True
    got = array_runs.require_readable_length(runs, Reporter())
    if got is None:
        return True
    if got == declared.size:
        return False
    report = er.emit_with(validator.reporter, er.ERR.CE2011, span, got=got,
                          expected=declared.size)
    if array_runs.has_run(runs):
        for number, run in enumerate(runs, start=1):
            fills = (f"run {number} fills {run.start}..{run.end} "
                     f"({run.count} element{'' if run.count == 1 else 's'})")
            if run.loc is None:
                report.note(fills)
            else:
                report.note_at(fills, run.loc)
    report.emit()
    return True


def _short_rows(validator: TypeValidator, node: e.ArrayInit, slots: list[Optional[Type]],
                element: Optional[Type]) -> bool:
    """When the rows of a literal do not agree, CE2011 at each row that is not the size of
    the row type of its position, at any depth. Rows that agree with each other and not
    with their position are one mismatch of the whole literal, which the position reports.
    A row with an error has no type, and a short row reported its own error, so it does not
    count. Answers whether a row was short."""
    typed = [slot for slot in slots if slot is not None]
    if len(typed) < 2 or all(slot == typed[0] for slot in typed):
        return False
    short = False
    for run in node.runs:
        short = _measure_row(validator, _value_of(run), element) or short
    return short


def _measure_row(validator: TypeValidator, row: e.ShirExpr, row_type: Optional[Type]) -> bool:
    if not (isinstance(row, e.ArrayInit) and isinstance(row.kind, e.Fixed)
            and isinstance(row_type, ArrayType)):
        return False
    runs = _plan(validator, row, Reporter())
    if runs is None:
        return False
    if array_runs.expanded_length(runs) != row_type.size:
        return reject_wrong_size(validator, row, row_type, row.loc)
    short = False
    for run in row.runs:
        short = _measure_row(validator, _value_of(run), row_type.base_type) or short
    return short


def _reject_mixed_elements(validator: TypeValidator, node: e.ArrayInit,
                           slots: list[Optional[Type]]) -> None:
    """CE2013 at each element whose slot type is not the slot type of the first."""
    first = slots[0]
    if first is None:
        return
    for run, slot in zip(node.runs[1:], slots[1:], strict=True):
        if slot is not None and slot != first:
            er.emit(validator.reporter, er.ERR.CE2013, _value_of(run).loc,
                    expected=display_type(first), got=display_type(slot))


# -- an index and a slice ----------------------------------------------------------------


def check_index(validator: TypeValidator, node: e.Index,
                _expected: Optional[Type]) -> Optional[Type]:
    """The element that `a[i]` reads: the element of an array, or the `u8` of a string."""
    base = deref_type(visit.check_expr(validator, node.base))
    position = STRING_INDEX if base == BuiltinType.STRING else ARRAY_INDEX
    reject_non_index(validator, node.index, check_index_operand(validator, node.index),
                     position=position)
    if base is None:
        return None
    if base == BuiltinType.STRING:
        return BuiltinType.U8
    if not isinstance(base, (ArrayType, DynamicArrayType)):
        _reject_not_indexable(validator, node, node.base, base)
        return None
    if isinstance(base, ArrayType):
        reject_index_out_of_range(validator, node.index, base.size)
    return base.base_type


def check_slice(validator: TypeValidator, node: e.Slice,
                _expected: Optional[Type]) -> Optional[Type]:
    """The `T[]` view that `a[i..j]` reads (docs/design/slices.md)."""
    base = deref_type(visit.check_expr(validator, node.base))
    span = _join(node.start.loc, node.end.loc)
    if base == BuiltinType.STRING:
        er.emit_with(validator.reporter, er.ERR.CE2122, span) \
            .help("a string has no slice: copy the part with `s.s(a, b)`, or index one byte "
                  "with `s[i]`") \
            .emit()
        check_slice_bounds(validator, (node.start, node.end))
        return None
    check_slice_bounds(validator, (node.start, node.end))
    _reject_bounds_that_go_down(validator, node, span)
    if base is None:
        return None
    if not isinstance(base, (ArrayType, DynamicArrayType)):
        _reject_not_indexable(validator, node, node.base, base)
        return None
    return DynamicArrayType(base.base_type)


def _reject_bounds_that_go_down(validator: TypeValidator, node: e.Slice, span: Span) -> None:
    folder, answers = validator.body_folder(), validator.body_answers()
    start = folder.scalar(node.start, answers.type_of(node.start.shir_id))
    end = folder.scalar(node.end, answers.type_of(node.end.shir_id))
    if (start is None or end is None or not is_integer_constant(start)
            or not is_integer_constant(end)):
        return
    reject_a_range_that_goes_down(
        plan_range(int(start.value), int(end.value), node.inclusive, False, span),
        validator.reporter)


def _reject_not_indexable(validator: TypeValidator, node: e.Index | e.Slice, base: e.ShirExpr,
                          ty: Type) -> None:
    """CE2114 at the base. A base of a type that the pass has no rule for is an internal
    error: the name of a type pack in a template check, and a type whose name the unit
    lost."""
    if isinstance(ty, UnknownType):
        visit.unhandled(validator, node)
    er.emit(validator.reporter, er.ERR.CE2114, base.loc, type=display_type(ty))
