"""The range arms of the typecheck pass over SHIR (docs/design/ir.md 7.4, 7.7).

A range has four positions: the iterable of a `foreach`, a run of an array literal, the
index of a slice, and a position that expects `Iterator@(i32)`. The `foreach` arm checks
its range with `check_range_iterable`: the range is no value, so its loop is a
`CountedRange`. A run is no value either (`check_range_run`), and the reader of the runs
says when it goes down. A slice has no `Range` node, and its arm checks its two bounds with
`check_bounds`. A range in any other position comes to the arm of the table,
`check_range`: in a position that expects an `Iterator@(i32)` the range is a value, and any
other position is CE2122. Each bound is an i32 position
(`semantics/passes/types/arrays.py`): a bound that is no number is CE2072, and a number of
another type is CE2121. Readable bounds of a `foreach` range must go up (CE2125,
`semantics/ranges.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arrays import (
    RANGE_BOUND, check_i32_operand, reject_non_i32)
from sushi_lang.semantics.ranges import RangePlan, reject_a_range_that_goes_down
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.type_predicates import is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, IteratorType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator

#: The type of a range.
RANGE_TYPE = IteratorType(BuiltinType.I32)


def check_range_iterable(validator: TypeValidator, node: e.Range) -> None:
    """The range of a `foreach`: its bounds, then CE2125 when readable bounds go down. The
    range is no value, and its type row is `Iterator@(i32)`."""
    check_range_run(validator, node)
    _reject_going_down(validator, node)


def check_range_run(validator: TypeValidator, node: e.Range) -> None:
    """A range that is no value: its bounds, its row "no value", and its type
    `Iterator@(i32)`."""
    check_bounds(validator, (node.start, node.end))
    validator.body_answers().record_range_value(node.shir_id, False)
    visit.answer(validator, node, RANGE_TYPE)


def check_range(validator: TypeValidator, node: e.Range,
                expected: Optional[Type]) -> Optional[Type]:
    """A range in a position that is no `foreach`: a value where an iterator is expected,
    else CE2122, then its bounds."""
    if expected == RANGE_TYPE:
        check_bounds(validator, (node.start, node.end))
        _reject_going_down(validator, node)
        validator.body_answers().record_range_value(node.shir_id, True)
        return RANGE_TYPE
    er.emit_with(validator.reporter, er.ERR.CE2122, node.loc) \
        .help("walk it with `foreach`, spell it into an array (`from([a..b])`), or give "
              "it to a position of the type `Iterator@(i32)`") \
        .emit()
    check_bounds(validator, (node.start, node.end))
    return None


def _reject_going_down(validator: TypeValidator, node: e.Range) -> None:
    plan = validator.folder.read(node, None)
    reject_a_range_that_goes_down(plan if isinstance(plan, RangePlan) else None,
                                  validator.reporter)


def check_bounds(validator: TypeValidator, bounds: tuple[e.ShirExpr, e.ShirExpr]) -> None:
    """Each bound at i32, the start first; the bounds are checked before either reports."""
    walked = [(bound, check_i32_operand(validator, bound)) for bound in bounds]
    for bound, ty in walked:
        if ty is not None and not is_numeric_type(ty):
            er.emit(validator.reporter, er.ERR.CE2072, bound.loc, got=display_type(ty),
                    expected="integer type")
            continue
        reject_non_i32(validator, bound, ty, position=RANGE_BOUND)
