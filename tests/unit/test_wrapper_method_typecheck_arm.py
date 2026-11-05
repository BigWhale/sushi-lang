"""The typecheck arm of the `Result@(T, E)` and `Maybe@(T)` methods (docs/design/ir.md 7.3).

The arm of each family is a row of `FAMILY_ARMS`. A test method (`is_ok`, `is_err`,
`is_some`, `is_none`) yields a bool, and the call keeps its `Builtin` callee row. A method
with a signature row (`map_err`, `or_err`) goes to the one arm of a signature method
(`visit/signature_methods.py`) with its row. The tests run the arm over SHIR built by hand,
with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

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


def test_the_rows_with_a_signature_are_map_err_and_or_err():
    assert set(RESULT_METHOD_SIGNATURES) == {"map_err"}
    assert set(MAYBE_METHOD_SIGNATURES) == {"or_err"}


@pytest.mark.parametrize("receiver, method, family, rows", [
    (RESULT, "map_err", RESULT_FAMILY, RESULT_METHOD_SIGNATURES),
    (MAYBE, "or_err", MAYBE_FAMILY, MAYBE_METHOD_SIGNATURES),
])
def test_a_row_with_a_signature_goes_to_the_signature_arm(monkeypatch, receiver, method,
                                                         family, rows):
    seen = []
    monkeypatch.setattr(visit, "check_expr", lambda _validator, _node: receiver)
    monkeypatch.setattr(calls, "check_signature_method",
                        lambda _validator, _node, recv, name, fam, signature:
                        seen.append((recv, name, fam.name, signature)) or I32)
    answers = TypeckResults()
    call = _call(method)
    assert calls.check_call(_validator(answers), call, None) == I32  # type: ignore[arg-type]
    assert seen == [(receiver, method, family, rows[method])]
    assert answers.callee_of(call.shir_id) == ResolvedCall(
        Builtin(call.target.recv, BuiltinMethod(family, method, nom_self=True)))  # type: ignore[union-attr]
