"""The typecheck arm of a destructure (docs/design/ir.md 7.3, 7.5; ruling R-D).

Each target takes its element, and the path of the target keys its element type row: the
index of the target, then the index in each `Nested` level. A bare binder takes the type
of its element. A `Place` of a place that is not a local, a field or an element stops with
`typecheck, Destructure`. The tests run the arm over SHIR built by hand, with a stand-in
for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, StructType, Type

SPAN = Span(3, 5, 3, 30)
INNER = StructType("$Tuple<bool, string>", (("0", BuiltinType.BOOL), ("1", BuiltinType.STRING)),
                   generic_base="$Tuple", generic_args=(BuiltinType.BOOL, BuiltinType.STRING))
OUTER = StructType("$Tuple<i32, $Tuple<bool, string>>", (("0", BuiltinType.I32), ("1", INNER)),
                   generic_base="$Tuple", generic_args=(BuiltinType.I32, INNER))


def _validator(value: Type) -> SimpleNamespace:
    """A stand-in for the pass whose local `_0` has the type `value`, with the bare binders
    `_1` and `_2`."""
    answers = TypeckResults()
    answers.record_local(LocalId(0), value)
    binders = {LocalId(i): ShirLocal(LocalId(i), name, None, SPAN)
               for i, name in ((1, "a"), (2, "b"))}
    return SimpleNamespace(answers=answers, body_answers=lambda: answers,
                           body_locals=binders, refused_locals=set(), reporter=None)


def _value() -> e.LocalRef:
    return e.LocalRef(ShirId(1), SPAN, LocalId(0))


def test_each_target_takes_its_element_by_its_path() -> None:
    validator = _validator(OUTER)
    node = s.Destructure(ShirId(2), SPAN, [LocalId(1), s.Nested([LocalId(2), s.Discard()], SPAN)],
                         _value())
    visit.check(validator, node)
    answers = validator.answers
    assert answers.element_type_of(node.shir_id, (0,)) == BuiltinType.I32
    assert answers.element_type_of(node.shir_id, (1,)) == INNER
    assert answers.element_type_of(node.shir_id, (1, 0)) == BuiltinType.BOOL
    assert answers.element_type_of(node.shir_id, (1, 1)) == BuiltinType.STRING
    assert answers.local_type(LocalId(1)) == BuiltinType.I32
    assert answers.local_type(LocalId(2)) == BuiltinType.BOOL


def test_a_place_that_is_not_typed_yet_stops() -> None:
    validator = _validator(OUTER)
    place = e.GlobalRef(ShirId(3), SPAN, DeclId(0))
    node = s.Destructure(ShirId(2), SPAN, [s.Place(place), LocalId(1)], _value())
    with pytest.raises(StageStop) as caught:
        visit.check(validator, node)
    assert caught.value.span == SPAN
    assert caught.value.params == {"stage": "typecheck", "construct": "Destructure"}
