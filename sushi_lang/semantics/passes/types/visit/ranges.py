"""The range arms of the typecheck pass over SHIR (docs/design/ir.md 7.4, 7.7).

A range has three positions: the iterable of a `foreach`, the index of a slice, and a
position that expects `Iterator@(i32)`. The `foreach` arm checks its range with
`check_range_iterable`: the range is no value, so its loop is a `CountedRange`. A range in
any other position comes to the arm of the table, `check_range`: a position that expects
an `Iterator@(i32)` stops the walk (`Range`, the iterators group), and any other position
is CE2122. Each bound is an i32 position (`semantics/passes/types/arrays.py`): a bound that
is no number is CE2072, and a number of another type is CE2121. Readable bounds of a
`foreach` range must go up (CE2125, `semantics/ranges.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arrays import RANGE_BOUND, emit_non_i32
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
    _check_bounds(validator, node)
    plan = validator.folder.read(node, None)
    reject_a_range_that_goes_down(plan if isinstance(plan, RangePlan) else None,
                                  validator.reporter)
    validator.body_answers().record_range_value(node.shir_id, False)
    visit.answer(validator, node, RANGE_TYPE)


def check_range(validator: TypeValidator, node: e.Range,
                expected: Optional[Type]) -> Optional[Type]:
    """A range in a position that is no `foreach`: CE2122, then its bounds. A position
    that expects an iterator stops the walk."""
    if isinstance(expected, IteratorType):
        visit.stop_at(validator, node)
    er.emit_with(validator.reporter, er.ERR.CE2122, node.loc) \
        .help("walk it with `foreach`, spell it into an array (`from([a..b])`), or give "
              "it to a position of the type `Iterator@(i32)`") \
        .emit()
    _check_bounds(validator, node)
    return None


def _check_bounds(validator: TypeValidator, node: e.Range) -> None:
    """Each bound at i32, the start first; the bounds are checked before either reports."""
    walked = [(bound, visit.check_expr(validator, bound, BuiltinType.I32))
              for bound in (node.start, node.end)]
    for bound, ty in walked:
        if ty is None or ty == BuiltinType.I32:
            continue
        if not is_numeric_type(ty):
            er.emit(validator.reporter, er.ERR.CE2072, bound.loc, got=display_type(ty),
                    expected="integer type")
            continue
        emit_non_i32(validator, bound.loc, ty, position=RANGE_BOUND)
