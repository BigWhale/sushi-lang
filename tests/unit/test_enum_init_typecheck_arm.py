"""The typecheck arm of a variant construction (docs/design/ir.md 7.3, 7.4).

A `Result` or a `Maybe` variant takes the instance of its position. A variant of an enum
that is not generic is an instance of that enum, also behind an alias. A wrapper variant
whose position gives no instance, a variant of a generic enum of the program and a variant
of an enum whose name the unit lost (D2) stop with `typecheck, EnumInit`. A `Static` call
on an enum stops with `typecheck, Call(static)` while a unit declares a method of its name.
The tests run the arm over SHIR built by hand, with a stand-in for the pass; they do not
run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId, ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo, UnknownType

SPAN = Span(3, 5, 3, 20)
I32 = BuiltinType.I32
SIGN = EnumType("Sign", (EnumVariantInfo("Plus", ()), EnumVariantInfo("Minus", ())))
MAYBE_I32 = EnumType("Maybe<i32>", (EnumVariantInfo("Some", (I32,)),
                                    EnumVariantInfo("None", ())),
                     generic_base="Maybe", generic_args=(I32,))
SLOT = SimpleNamespace(name="Slot")


def _visibility(lost: Optional[str]) -> SimpleNamespace:
    """The visibility table of a unit that lost the name `lost`. Every name is public by
    absence."""
    return SimpleNamespace(contested_by=lambda _kind, name, _unit: name == lost,
                           origin=lambda _kind, _name: None)


def _validator(written: str, *, lost: Optional[str] = None,
               methods: tuple[str, ...] = ()) -> SimpleNamespace:
    answers = TypeckResults()
    bindings = {("geo", "Sign"): SimpleNamespace(kind="enum", name="Sign")}
    by_type = {"Sign": {name: [SimpleNamespace(unit_name="main")] for name in methods}}
    return SimpleNamespace(
        answers=answers, body_answers=lambda: answers, decl_name=lambda _decl: written,
        enum_table=SimpleNamespace(by_name={"Sign": SIGN, "Maybe<i32>": MAYBE_I32}),
        generic_enum_table=SimpleNamespace(by_name={"Maybe": object(), "Slot": SLOT}),
        namespaces=SimpleNamespace(lookup=lambda alias, name: bindings.get((alias, name))),
        visibility=_visibility(lost), current_unit_name="main", in_template_check=False,
        in_synthesized_body=False, errors_offered=lambda: 0, reporter=None,
        extension_table=SimpleNamespace(by_type=by_type),
        generic_extension_table=SimpleNamespace(by_type={}),
        perk_impl_table=SimpleNamespace(units={}, implementations={}))


def _init(variant: str) -> e.EnumInit:
    return e.EnumInit(ShirId(1), SPAN, DeclId(0), variant, [])


def _stop(validator: SimpleNamespace, node: e.ShirExpr,
          expected: Optional[EnumType] = None) -> dict:
    with pytest.raises(StageStop) as caught:
        visit.check_expr(validator, node, expected)
    assert caught.value.span == SPAN
    return caught.value.params


@pytest.mark.parametrize("written", ["Sign", "geo.Sign"])
def test_a_variant_of_a_plain_enum_is_an_instance_of_it(written: str) -> None:
    validator = _validator(written)
    node = _init("Minus")
    assert visit.check_expr(validator, node) == SIGN
    assert validator.answers.instance_of(node.shir_id) == SIGN


def test_a_maybe_variant_takes_the_instance_of_its_position() -> None:
    validator = _validator("Maybe")
    node = _init("None")
    assert visit.check_expr(validator, node, MAYBE_I32) == MAYBE_I32
    assert validator.answers.instance_of(node.shir_id) == MAYBE_I32


@pytest.mark.parametrize("written, lost, expected", [
    ("Maybe", None, None),
    ("Maybe", None, SIGN),
    ("Slot", None, None),
    ("Sign", "Sign", None),
])
def test_a_construction_that_is_not_typed_yet_stops(written: str, lost: Optional[str],
                                                    expected: Optional[EnumType]) -> None:
    validator = _validator(written, lost=lost)
    assert _stop(validator, _init("None" if written != "Sign" else "Plus"), expected) == {
        "stage": "typecheck", "construct": "EnumInit"}


def test_a_static_call_on_an_enum_with_a_method_of_its_name_stops() -> None:
    validator = _validator("Sign", methods=("flip",))
    node = e.Call(ShirId(1), SPAN, e.Static(UnknownType("Sign"), "flip"), [])
    assert _stop(validator, node) == {"stage": "typecheck", "construct": "Call(static)"}
