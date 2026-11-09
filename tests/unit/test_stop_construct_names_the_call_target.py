"""The construct of a stop at a `Call` names the kind of its target.

One function, `dispatch.construct_of`, derives the label from the SHIR node and from the
facts that the stop site gives it (`CallFacts`), so each Phase 3 group can measure its own
backlog. The test runs it over nodes built by hand and a table of facts, with data only.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import dispatch
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.typesys import BuiltinType, UnknownType

SPAN = Span(1, 1, 1, 2)
I32 = BuiltinType.I32


NAMES = {0: "f", 1: "alias.f", 2: "sqrt", 3: "libc.puts", 4: "std.sqrt", 5: "keep", 6: "errno"}
NAMESPACES = {("alias", "f"): "function", ("libc", "puts"): "extern", ("std", "sqrt"): "stdlib"}


UNITS = {"doubled": frozenset({"app"}), "lines": frozenset({"collections/strings"}),
         "both": frozenset({"collections/strings", "app"}), "read": frozenset({"io/contracts"}),
         "tick": frozenset({"app"}), "lib": frozenset({None})}


def facts() -> SimpleNamespace:
    """A table of facts: the written names of the declarations and the names by kind."""
    return SimpleNamespace(
        written_name=lambda decl: NAMES[decl.index],
        declaring_units=lambda name: UNITS.get(name, frozenset()),
        namespace_kind=lambda alias, name: NAMESPACES.get((alias, name)),
        is_generic_function=lambda name: name == "keep",
        is_stdlib_function=lambda name: name == "sqrt",
        is_extern_function=lambda name: name == "errno")


def _node(cls, *args):
    return cls(ShirId(0), SPAN, *args)


def _call(target):
    return _node(e.Call, target, [])


def _global(index):
    return _node(e.GlobalRef, DeclId(index))


LOCAL = _node(e.LocalRef, LocalId(0))

CASES = [
    (e.Dotted(LOCAL, "len"), "Call(method)"),
    (e.Dotted(LOCAL, "doubled"), "Call(extension)"),
    (e.Dotted(LOCAL, "lines"), "Call(stdlib method)"),
    (e.Dotted(LOCAL, "read"), "Call(stdlib method)"),
    (e.Dotted(LOCAL, "both"), "Call(extension)"),
    (e.Dotted(LOCAL, "tick"), "Call(extension)"),
    (e.Dotted(LOCAL, "lib"), "Call(extension)"),
    (e.Static(UnknownType("Vec"), "at"), "Call(static)"),
    (e.Static(UnknownType("List"), "new"), "Call(builtin static)"),
    (e.Static(UnknownType("hm.HashMap"), "new"), "Call(builtin static)"),
    (e.Static(I32, "from_bits"), "Call(static)"),
    (e.BuiltinFn("println"), "Call(builtin)"),
    (e.Named(_global(0)), "Call(function)"),
    (e.Named(_global(0), (I32,)), "Call(generic)"),
    (e.Named(_global(5)), "Call(generic)"),
    (e.Named(_global(2)), "Call(stdlib)"),
    (e.Named(_global(6)), "Call(extern)"),
    (e.Named(_global(1)), "Call(alias)"),
    (e.Named(_global(3)), "Call(extern)"),
    (e.Named(_global(4)), "Call(stdlib)"),
    (e.Named(LOCAL), "Call(fn value)"),
    (e.Named(_node(e.Field, LOCAL, "handler", SPAN)), "Call(fn value)"),
]


@pytest.mark.parametrize("target, label", CASES)
def test_a_call_names_its_target_kind(target, label):
    assert dispatch.construct_of(_call(target), facts()) == label


def test_another_class_names_its_class():
    assert dispatch.construct_of(LOCAL) == "LocalRef"


def test_a_call_with_no_facts_is_an_internal_error():
    with pytest.raises(Exception, match="CE0000|call facts"):
        dispatch.construct_of(_call(e.Dotted(LOCAL, "len")))


@pytest.mark.parametrize("target, label", CASES)
def test_a_stop_at_a_call_carries_the_label(target, label):
    with pytest.raises(StageStop) as raised:
        dispatch.stop("typecheck", _call(target), facts())
    assert label in str(raised.value)
