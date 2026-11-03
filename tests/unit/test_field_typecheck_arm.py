"""The typecheck arm of a field read (docs/design/ir.md 7.3, 7.4).

A field of a struct, and an element of a tuple, answers the type of its field and writes
the field index row. A base of a type whose name the unit lost (D2), and a base of a type
that has no rule yet, stop with `typecheck, Field`. The tests run the arm over SHIR built
by hand, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, StructType, Type

SPAN = Span(3, 5, 3, 12)
MEMBER = Span(3, 10, 3, 12)
POINT = StructType("Point", (("x", BuiltinType.I32), ("y", BuiltinType.BOOL)))
PAIR = StructType("$Tuple<i32, string>", (("0", BuiltinType.I32), ("1", BuiltinType.STRING)),
                  generic_base="$Tuple", generic_args=(BuiltinType.I32, BuiltinType.STRING))


def _validator(base: Type, *, lost: Optional[str] = None) -> SimpleNamespace:
    """A stand-in for the pass whose local `_0` has the type `base`; the unit lost the
    type name `lost`."""
    answers = TypeckResults()
    answers.record_local(LocalId(0), base)
    visibility = SimpleNamespace(contested_by=lambda _kind, name, _unit: name == lost,
                                 origin=lambda _kind, _name: None)
    return SimpleNamespace(
        answers=answers, body_answers=lambda: answers,
        struct_table=SimpleNamespace(by_name={"Point": POINT}),
        enum_table=SimpleNamespace(by_name={}),
        visibility=visibility, current_unit_name="main", in_template_check=False,
        in_synthesized_body=False, reporter=None)


def _field(name: str) -> e.Field:
    return e.Field(ShirId(2), SPAN, e.LocalRef(ShirId(1), SPAN, LocalId(0)), name, MEMBER)


@pytest.mark.parametrize("base, name, ty, index", [
    (POINT, "x", BuiltinType.I32, 0),
    (POINT, "y", BuiltinType.BOOL, 1),
    (PAIR, "1", BuiltinType.STRING, 1),
])
def test_a_field_answers_the_type_of_its_field(base: Type, name: str, ty: Type,
                                               index: int) -> None:
    validator = _validator(base)
    node = _field(name)
    assert visit.check_expr(validator, node) == ty
    assert validator.answers.type_of(node.shir_id) == ty
    assert validator.answers.field_index_of(node.shir_id) == index


@pytest.mark.parametrize("base, lost", [
    (POINT, "Point"),
    (GenericTypeRef("Box", (BuiltinType.I32,)), None),
])
def test_a_field_that_is_not_typed_yet_stops(base: Type, lost: Optional[str]) -> None:
    validator = _validator(base, lost=lost)
    with pytest.raises(StageStop) as caught:
        visit.check_expr(validator, _field("x"))
    assert caught.value.span == SPAN
    assert caught.value.params == {"stage": "typecheck", "construct": "Field"}
