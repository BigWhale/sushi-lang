"""A method call that a built-in family claims, and whose arm the typecheck pass does not
have yet, stops with the label of that family (`typecheck, Call(method <family>)`).

The receiver need not be of a built-in type: a `List` method and an array method each
stop with the label of their own family, so each later group can count its own stops. The
test types the receiver from a table and runs the arm of the call over SHIR built by hand.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.visit import calls
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.typesys import BuiltinType, IteratorType, StructType

SPAN = Span(4, 5, 4, 20)


class _NoMethods:
    """No program under test declares an extension or a perk implementation."""

    def get_method(self, target_type, method_name):
        return None


def _validator() -> SimpleNamespace:
    empty = SimpleNamespace(by_type={}, implementations={}, units={})
    return SimpleNamespace(perk_impl_table=_NoMethods(), derived_methods=_NoMethods(),
                           extension_table=empty, generic_extension_table=empty,
                           refused_locals=frozenset())


@pytest.mark.parametrize("receiver, method, label", [
    (StructType(name="Own<i32>", fields=(), generic_base="Own"), "get",
     "Call(method own)"),
    (IteratorType(BuiltinType.I32), "clone", "Call(method iterator)"),
])
def test_a_family_with_no_arm_names_its_family(monkeypatch, receiver, method, label):
    monkeypatch.setattr(visit, "check_expr", lambda _validator, _node: receiver)
    call = e.Call(ShirId(1), SPAN, e.Dotted(e.LocalRef(ShirId(0), SPAN, LocalId(0)), method),
                  [])
    with pytest.raises(StageStop) as caught:
        calls.check_call(_validator(), call, None)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "typecheck", "construct": label}
    assert caught.value.span == SPAN
