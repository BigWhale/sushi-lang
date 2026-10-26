"""The construct of a stop at a `Call` names the kind of its target.

One function, `dispatch.construct_of`, derives the label from the SHIR node, so the
typecheck pass, the borrow pass and the SLIR lowering print one spelling. The test runs
it over nodes built by hand and over data only.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.typesys import BuiltinType

SPAN = Span(1, 1, 1, 2)
I32 = BuiltinType.I32


def _node(cls, *args):
    return cls(ShirId(0), SPAN, *args)


def _call(target):
    return _node(e.Call, target, [])


LOCAL = _node(e.LocalRef, LocalId(0))
GLOBAL = _node(e.GlobalRef, DeclId(0))

CASES = [
    (e.Dotted(LOCAL, "len"), "Call(method)"),
    (e.Static(I32, "from_bits"), "Call(static)"),
    (e.BuiltinFn("println"), "Call(builtin)"),
    (e.Named(GLOBAL, (I32,)), "Call(generic)"),
    (e.Named(GLOBAL), "Call(function)"),
    (e.Named(LOCAL), "Call(fn value)"),
]


@pytest.mark.parametrize("target, label", CASES)
def test_a_call_names_its_target_kind(target, label):
    assert dispatch.construct_of(_call(target)) == label


def test_another_class_names_its_class():
    assert dispatch.construct_of(LOCAL) == "LocalRef"


@pytest.mark.parametrize("target, label", CASES)
def test_a_stop_at_a_call_carries_the_label(target, label):
    with pytest.raises(StageStop) as raised:
        dispatch.stop("typecheck", _call(target))
    assert label in str(raised.value)
