"""The arm of a destructure in the typecheck pass over SHIR (docs/design/ir.md 7.3, 7.5).

`let (a, b) = v` and the tuple rebind `(a, b) := v` are one `Destructure` (ruling R-D).
First each target gives what it wants of its element: a binder its written type, which is
checked as the type of a `let` is checked, a `Place` the type of its place, and a bare
binder and a `_` no type. A `Nested` target wants what its targets want. A place is a
local, a `var`, a field or an element, checked as the target of a rebind, of a field write
or of an element write: a field or an element of a constant is CE2096 at the place, a byte of a
string is CE2113, a C global is CE5016, and a target that is not a place is CE2130. Then the value is
checked at the wants, so a literal part takes the type of its binder or of its place
(`fields.check_tuple_at`). A `Result` value is CE2505.

Then each target takes its element, and the path of the target keys its element type
row. A level that is no tuple is CE2117, and a level with a different count is CE2120, at
the value or at the nested target. A bare binder takes the type of its element. A binder
with a written type and a place keep their own type, and an element that does not fit is
CE2002 at the written type or at the place: the binder is then refused, as the local of a
`let` is refused. Whether a target owns or borrows its element is a rule of `borrow`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sushi_lang.internals import errors as er
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.results import is_result_enum
from sushi_lang.semantics.generics.tuples import is_tuple_type, tuple_elements
from sushi_lang.semantics.generics.type_display import display_type
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.compatibility import reject_mismatch
from sushi_lang.semantics.passes.types.visit.extern_calls import reject_extern_variable_write
from sushi_lang.semantics.passes.types.visit.statements import (
    reject_non_place, reject_unwritable_place,
)
from sushi_lang.semantics.passes.types.resolution import resolve_variable_type
from sushi_lang.semantics.passes.types.utils import validate_type_name
from sushi_lang.semantics.passes.types.visit.fields import Want, check_tuple_at
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import LocalId
from sushi_lang.semantics.typesys import Type, deref_type

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def check_destructure(validator: TypeValidator, node: s.Destructure) -> None:
    """The wants of the targets, the value at them, then the element of each target."""
    wants = [_want(validator, node, target) for target in node.targets]
    ty = check_tuple_at(validator, node.value, wants)
    value = None if ty is None else deref_type(ty)
    if value is not None and is_result_enum(value):
        er.emit(validator.reporter, er.ERR.CE2505, node.value.loc)
        value = None
    _bind(validator, node, node.targets, wants, value, node.value.loc, ())


def _want(validator: TypeValidator, node: s.Destructure, target: s.DestructureTarget) -> Want:
    if isinstance(target, s.Nested):
        return [_want(validator, node, inner) for inner in target.targets]
    if isinstance(target, s.Place):
        return _place_type(validator, node, target.target)
    if isinstance(target, s.Discard):
        return None
    return _written_type(validator, target)


def _written_type(validator: TypeValidator, binder: LocalId) -> Optional[Type]:
    """The written type of a binder, which is the type of its local; None for a bare
    binder, and for a written type that the unit cannot name."""
    local = validator.body_locals[binder]
    if local.declared is None:
        return None
    declared = validator.written_type(local.declared)
    refused = validate_type_name(validator, declared, local.declared_loc)
    ty = resolve_variable_type(validator, declared, local.declared_loc)
    validator.body_answers().record_local(binder, ty)
    return None if refused else ty


def _place_type(validator: TypeValidator, node: s.Destructure,
                place: e.ShirExpr) -> Optional[Type]:
    """The type of a place with no `peek` or `poke`; None after an error of the place: a C
    global is CE5016, and a target that is not a place CE2130."""
    if reject_extern_variable_write(validator, place) or reject_non_place(validator, place):
        return None
    ty = visit.check_expr(validator, place)
    if reject_unwritable_place(validator, place, place.loc):
        return None
    return None if ty is None else deref_type(ty)


def _bind(validator: TypeValidator, node: s.Destructure, targets: list[s.DestructureTarget],
          wants: list[Want], value: Optional[Type], loc: Span, prefix: tuple[int, ...]) -> None:
    """Give each target of one level its element; `loc` is where an error of the level
    points."""
    answers = validator.body_answers()
    elements = _elements(validator, value, len(targets), loc)
    for index, (target, want, element) in enumerate(zip(targets, wants, elements, strict=True)):
        path = (*prefix, index)
        if element is not None:
            answers.record_element(node.shir_id, path, element)
        if isinstance(want, list):
            if isinstance(target, s.Nested):
                _bind(validator, node, target.targets, want, element, target.loc, path)
        elif isinstance(target, s.Place):
            if element is not None and want is not None:
                reject_mismatch(validator, element, want, target.target.loc, None)
        elif isinstance(target, LocalId):
            _bind_local(validator, target, want, element)


def _bind_local(validator: TypeValidator, binder: LocalId, want: Optional[Type],
                element: Optional[Type]) -> None:
    answers = validator.body_answers()
    if answers.local_type(binder) is None:
        if element is not None:
            answers.record_local(binder, element)
        return
    if element is None or want is None:
        return
    local = validator.body_locals[binder]
    if reject_mismatch(validator, element, want, local.declared_loc or local.loc, None):
        validator.refused_locals.add(binder)


def _elements(validator: TypeValidator, value: Optional[Type], count: int,
              loc: Span) -> list[Optional[Type]]:
    """The element types of one level, or no type for each target after an error."""
    if value is None:
        return [None] * count
    if not is_tuple_type(value):
        er.emit(validator.reporter, er.ERR.CE2117, loc, type=display_type(value))
        return [None] * count
    elements = tuple_elements(value)
    if len(elements) != count:
        er.emit(validator.reporter, er.ERR.CE2120, loc, count=count, type=display_type(value),
                arity=len(elements))
        return [None] * count
    return list(elements)
