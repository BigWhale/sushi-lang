"""The typecheck arm of the `Result@(T, E)` and `Maybe@(T)` methods (docs/design/ir.md 7.3).

The arm of each family is a row of `FAMILY_ARMS`. A test method (`is_ok`, `is_err`,
`is_some`, `is_none`) yields a bool, and the call keeps its `Builtin` callee row. A method
with a signature row (`map_err`, `or_err`) is not typed yet: it stops with the label of its
family. That stop is temporary, so this test reads the rows and the label as data, and the
group that types them changes it in one place. The tests run the arm over SHIR built by
hand, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.generics.maybe import MAYBE_METHOD_SIGNATURES
from sushi_lang.semantics.generics.results import RESULT_METHOD_SIGNATURES
from sushi_lang.semantics.method_effects import MAYBE_FAMILY, RESULT_FAMILY
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.visit import calls
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.callee import Builtin, BuiltinMethod
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo

SPAN = Span(4, 5, 4, 20)
I32 = BuiltinType.I32
FAULT = EnumType("Fault", (EnumVariantInfo("Bad", ()),))
RESULT = EnumType("Result<i32, Fault>", (EnumVariantInfo("Ok", (I32,)),
                                         EnumVariantInfo("Err", (FAULT,))),
                  generic_base="Result", generic_args=(I32, FAULT))
MAYBE = EnumType("Maybe<i32>", (EnumVariantInfo("Some", (I32,)), EnumVariantInfo("None", ())),
                 generic_base="Maybe", generic_args=(I32,))


class _NoMethods:
    """No program under test declares an extension or a perk implementation."""

    def get_method(self, target_type, method_name):
        return None


def _validator(answers: TypeckResults) -> SimpleNamespace:
    empty = SimpleNamespace(by_type={}, implementations={}, units={})
    return SimpleNamespace(perk_impl_table=_NoMethods(), derived_methods=_NoMethods(),
                           extension_table=empty, generic_extension_table=empty,
                           refused_locals=frozenset(), reporter=None,
                           enum_table=None, struct_table=SimpleNamespace(by_name={}),
                           body_answers=lambda: answers)


def _call(method: str) -> e.Call:
    receiver = e.LocalRef(ShirId(0), SPAN, LocalId(0))
    return e.Call(ShirId(1), SPAN, e.Dotted(receiver, method), [])


def test_each_wrapper_family_has_its_arm():
    assert RESULT_FAMILY in calls.FAMILY_ARMS
    assert MAYBE_FAMILY in calls.FAMILY_ARMS


@pytest.mark.parametrize("receiver, method, family", [
    (RESULT, "is_ok", RESULT_FAMILY),
    (RESULT, "is_err", RESULT_FAMILY),
    (MAYBE, "is_some", MAYBE_FAMILY),
    (MAYBE, "is_none", MAYBE_FAMILY),
])
def test_a_test_method_yields_a_bool_and_keeps_its_callee(monkeypatch, receiver, method,
                                                         family):
    monkeypatch.setattr(visit, "check_expr", lambda _validator, _node: receiver)
    answers = TypeckResults()
    call = _call(method)
    yielded = calls.check_call(_validator(answers), call, None)  # type: ignore[arg-type]
    assert yielded == BuiltinType.BOOL
    assert answers.callee_of(call.shir_id) == ResolvedCall(
        Builtin(call.target.recv, BuiltinMethod(family, method, nom_self=False)))  # type: ignore[union-attr]


def test_the_rows_with_a_signature_are_the_untyped_rows():
    assert set(RESULT_METHOD_SIGNATURES) == {"map_err"}
    assert set(MAYBE_METHOD_SIGNATURES) == {"or_err"}


@pytest.mark.parametrize("receiver, method, label", [
    (RESULT, "map_err", "Call(method result)"),
    (MAYBE, "or_err", "Call(method maybe)"),
])
def test_a_row_with_a_signature_stops_with_the_label_of_its_family(monkeypatch, receiver,
                                                                    method, label):
    monkeypatch.setattr(visit, "check_expr", lambda _validator, _node: receiver)
    with pytest.raises(StageStop) as caught:
        calls.check_call(_validator(TypeckResults()), _call(method), None)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "typecheck", "construct": label}
    assert caught.value.span == SPAN
