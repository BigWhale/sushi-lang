"""The range arms of the typecheck pass over SHIR (docs/design/ir.md 7.4, 7.7).

A range has four positions: the iterable of a `foreach`, a run of an array literal, the
index of a slice, and a position that expects an `Iterator@(T)` of an integer `T`. The
`foreach` arm checks its range with `check_range_iterable`: the range is no value, so its
loop is a `CountedRange`. A run is no value either (`check_range_run`), and the reader of
the runs says when it goes down. A slice has no `Range` node, and its arm checks its two
bounds with `check_slice_bounds`. A range in any other position comes to the arm of the
table, `check_range`: in a position that expects an `Iterator@(T)` of an integer `T` the
range is a value, and any other position is CE2122.

A range has the type of its bounds (docs/adr/0001): its item type `T`, and the type
`Iterator@(T)`. `range_item` is the one rule. A literal bound takes the type of the other
bound, two literal bounds take i32, and a value position gives `T` to both. A bound that is
no number is CE2072, and a bound of another type than `T` is CE2121. A run of an array
literal has the type of its bounds too, and its slots have the type `T`; two literal
bounds of a run take the integer element type of the array, else i32. A slice bound is an index position (`semantics/passes/types/arrays.py`),
which takes any integer type. Readable bounds of a `foreach` range must go up (CE2125,
`semantics/ranges.py`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.arrays import (
    SLICE_BOUND, check_index_operand, is_byte_literal, is_int_literal, reject_negated_byte,
    reject_non_index, reject_other_bound)
from sushi_lang.semantics.ranges import RangePlan, reject_a_range_that_goes_down
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.type_predicates import is_integer_type, is_numeric_type
from sushi_lang.semantics.typesys import BuiltinType, IteratorType, Type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_range_iterable(validator: TypeValidator, node: e.Range) -> Type:
    """The range of a `foreach`: its bounds, then CE2125 when readable bounds go down. The
    range is no value. Answers its item type, the type of the `foreach` variable."""
    item = _check_no_value(validator, node, None)
    _reject_going_down(validator, node, item)
    return item


def check_range_run(validator: TypeValidator, node: e.Range,
                    element: Optional[Type]) -> Type:
    """A run of an array literal: a range that is no value. An integer element type of the
    array is the type of two literal bounds. Answers the item type, the type of each slot
    that the run fills."""
    return _check_no_value(validator, node, None,
                           element if _is_integer(element) else BuiltinType.I32)


def _check_no_value(validator: TypeValidator, node: e.Range, item: Optional[Type],
                    literal: Type = BuiltinType.I32) -> Type:
    ty = range_item(validator, node, item, literal)
    validator.body_answers().record_range_value(node.shir_id, False)
    visit.answer(validator, node, IteratorType(ty))
    return ty


def check_range(validator: TypeValidator, node: e.Range,
                expected: Optional[Type]) -> Optional[Type]:
    """A range in a position that is no `foreach`: a value where an `Iterator@(T)` of an
    integer `T` is expected, else CE2122, then its bounds."""
    if isinstance(expected, IteratorType) and is_integer_type(expected.element_type):
        item = range_item(validator, node, expected.element_type)
        _reject_going_down(validator, node, item)
        validator.body_answers().record_range_value(node.shir_id, True)
        return IteratorType(item)
    er.emit_with(validator.reporter, er.ERR.CE2122, node.loc) \
        .help("walk it with `foreach`, spell it into an array (`from([a..b])`), or give "
              "it to a position of the type `Iterator@(T)`") \
        .emit()
    range_item(validator, node, None)
    return None


def _reject_going_down(validator: TypeValidator, node: e.Range, item: Type) -> None:
    plan = validator.folder.read(node, IteratorType(item))
    reject_a_range_that_goes_down(plan if isinstance(plan, RangePlan) else None,
                                  validator.reporter)


def range_item(validator: TypeValidator, node: e.Range, item: Optional[Type],
               literal: Type = BuiltinType.I32) -> Type:
    """The item type of a range, and the check of its bounds, the start first. A typed
    bound is checked first; its type is the item type, unless the position gives `item`.
    Then each literal bound takes the item type, and two literal bounds take `literal`
    (i32, or the integer element type of the array of a run). A bare
    byte literal (`a'x'`), also under a negation, takes the item type too."""
    bounds = (node.start, node.end)
    typed = {id(bound): visit.check_expr(validator, bound, None)
             for bound in bounds if not is_int_literal(bound)}
    source = None
    if item is None:
        source = next((bound for bound in bounds if id(bound) in typed
                       and _is_integer(typed[id(bound)])), None)
        item = (typed[id(source)] if source is not None else None) or literal
    for bound in bounds:
        if id(bound) not in typed:
            if is_byte_literal(bound):
                reject_negated_byte(validator, bound)
            typed[id(bound)] = visit.check_expr(validator, bound, item)
    for bound in bounds:
        ty = typed[id(bound)]
        if ty is not None and not is_numeric_type(ty):
            er.emit(validator.reporter, er.ERR.CE2072, bound.loc, got=display_type(ty),
                    expected="integer type")
            continue
        reject_other_bound(validator, bound, ty, item,
                           None if source is None or source is bound else source.loc)
    return item


def _is_integer(ty: Optional[Type]) -> bool:
    return ty is not None and is_integer_type(ty)


def check_slice_bounds(validator: TypeValidator,
                       bounds: tuple[e.ShirExpr, e.ShirExpr]) -> None:
    """Each bound of a slice is an index position, the start first; the bounds are checked
    before either reports."""
    walked = [(bound, check_index_operand(validator, bound)) for bound in bounds]
    for bound, ty in walked:
        reject_non_index(validator, bound, ty, position=SLICE_BOUND)
