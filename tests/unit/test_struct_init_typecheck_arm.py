"""The typecheck arm of a struct construction (docs/design/ir.md 7.3, 7.4).

A construction of a struct that is not generic is an instance of that struct, also behind
an alias. A construction of a generic struct of the program, of a struct whose name the
unit lost, and of an alias binding that is no struct are an internal error for
`StructInit`. The tests run the arm over SHIR built by hand, with a stand-in for the pass;
they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import StructType

SPAN = Span(3, 5, 3, 20)
CRATE = StructType("Crate", ())
BOX = SimpleNamespace(name="Box", fields=())


def _visibility(lost: Optional[str]) -> SimpleNamespace:
    """The visibility table of a unit that lost the name `lost`. Every name is public by
    absence."""
    return SimpleNamespace(contested_by=lambda _kind, name, _unit: name == lost,
                           origin=lambda _kind, _name: None)


def _validator(written: str, *, lost: Optional[str] = None) -> SimpleNamespace:
    answers = TypeckResults()
    bindings = {("geo", "Crate"): SimpleNamespace(kind="struct", name="Crate"),
                ("geo", "Sign"): SimpleNamespace(kind="enum", name="Sign")}
    return SimpleNamespace(
        has_an_error=lambda: False,
        answers=answers, body_answers=lambda: answers, decl_name=lambda _decl: written,
        struct_table=SimpleNamespace(by_name={"Crate": CRATE}),
        generic_struct_table=SimpleNamespace(by_name={"Box": BOX}),
        namespaces=SimpleNamespace(lookup=lambda alias, name: bindings.get((alias, name))),
        visibility=_visibility(lost), current_unit_name="main", in_template_check=False,
        in_synthesized_body=False, errors_offered=lambda: 0, reporter=None)


def _init() -> e.StructInit:
    return e.StructInit(ShirId(1), SPAN, DeclId(0), [], (), None, False, SPAN)


@pytest.mark.parametrize("written", ["Crate", "geo.Crate"])
def test_a_construction_of_a_plain_struct_is_an_instance_of_it(written: str) -> None:
    validator = _validator(written)
    node = _init()
    assert visit.check_expr(validator, node) == CRATE
    assert validator.answers.instance_of(node.shir_id) == CRATE


@pytest.mark.parametrize("written, lost", [
    ("Crate", "Crate"),
    ("geo.Sign", None),
])
def test_a_construction_that_is_not_typed_is_an_internal_error(written: str,
                                                               lost: Optional[str]) -> None:
    validator = _validator(written, lost=lost)
    with pytest.raises(InternalCompilerError) as caught:
        visit.check_expr(validator, _init())
    assert caught.value.span == SPAN
    assert caught.value.params == {
        "detail": "the typecheck stage does not handle the construct 'StructInit'"}
