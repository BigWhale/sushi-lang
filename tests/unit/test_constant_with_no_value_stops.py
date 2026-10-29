"""A constant with no value stops the typecheck walk with CE0155 (docs/design/ir.md 7.3).

A `ConstRef` of a constant that has no value stops with `typecheck, ConstRef with no value`,
and the `Return` of an initializer that fits and has no value, and is no empty container,
stops with `typecheck, Return with no value`. A function-typed constant is such a case: the
constant evaluator gives no value and reports nothing. The body is never dropped in silence.
After an error of the initializer, that error is the diagnostic. The tests run the arms over
SHIR built by hand, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import ScalarConstant
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.constants import InitializerFacts
from sushi_lang.semantics.passes.types.visit import expressions, statements
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.typesys import BuiltinType

SPAN = Span(4, 5, 4, 20)
I32 = BuiltinType.I32


def _validator(value, *, answers: TypeckResults, initializer=None) -> SimpleNamespace:
    return SimpleNamespace(constant_value=lambda _record: value,
                           body_answers=lambda: answers, initializer=initializer)


def _ref() -> e.ConstRef:
    return e.ConstRef(ShirId(1), SPAN, DeclNames().refer("F"))


def test_a_constant_with_no_value_stops() -> None:
    validator = _validator(None, answers=TypeckResults())
    with pytest.raises(StageStop) as caught:
        expressions._record_constant_value(validator, _ref(), object())  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "typecheck", "construct": "ConstRef with no value"}
    assert caught.value.span == SPAN


def test_a_constant_with_a_value_writes_its_row() -> None:
    answers = TypeckResults()
    ref = _ref()
    validator = _validator(ScalarConstant(6, I32), answers=answers)
    expressions._record_constant_value(validator, ref, object())  # type: ignore[arg-type]
    assert answers.value_of(ref.shir_id) == ScalarConstant(6, I32)


def _initializer(monkeypatch, *, value, empty: bool) -> tuple[s.Return, TypeckResults,
                                                                SimpleNamespace]:
    monkeypatch.setattr(visit, "check_expr", lambda _validator, _node, _expected: I32)
    monkeypatch.setattr(statements, "reject_mismatch", lambda *_args: False)
    decl = SimpleNamespace(ty=I32, type_span=SPAN, name_span=SPAN, value=None, loc=SPAN)
    ret = s.Return(ShirId(2), SPAN, _ref())
    answers = TypeckResults()
    facts = InitializerFacts(decl, value, empty)  # type: ignore[arg-type]
    return ret, answers, _validator(value, answers=answers, initializer=facts)


def test_an_initializer_with_no_value_stops(monkeypatch) -> None:
    ret, _answers, validator = _initializer(monkeypatch, value=None, empty=False)
    with pytest.raises(StageStop) as caught:
        statements._check_initializer(validator, ret)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "typecheck", "construct": "Return with no value"}


def test_an_empty_container_has_no_value_and_does_not_stop(monkeypatch) -> None:
    ret, answers, validator = _initializer(monkeypatch, value=None, empty=True)
    statements._check_initializer(validator, ret)  # type: ignore[arg-type]
    assert answers.value_of(ret.shir_id) is None


def test_an_initializer_with_a_value_does_not_stop_and_writes_no_row(monkeypatch) -> None:
    """No stage reads a value row of an initializer: the backend makes the global from the
    constant evaluator (docs/design/ir.md 7.3, 8.2)."""
    ret, answers, validator = _initializer(monkeypatch, value=ScalarConstant(6, I32),
                                           empty=False)
    statements._check_initializer(validator, ret)  # type: ignore[arg-type]
    assert answers.value_of(ret.shir_id) is None
