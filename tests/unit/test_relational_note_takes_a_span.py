"""A relational note takes a span, and a prose note takes none (#731).

CLAUDE.md's diagnostic ladder: a relational diagnostic carries one or more notes, each
with its OWN file:line:col, and a relational note rendered with no location is a
regression. `note(message, span=None)` accepted the `None` silently, so a caller that held
an `Optional[Span]` could lose the location with no decision.

The contract is in the name now. `note_at(message, span)` is relational and its span is
not optional; `note(message)` is prose and has no place for a span. There is deliberately
no guard that refuses a prose note: many are correct as prose (see the
`DiagnosticBuilder` docstring).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sushi_lang.internals.diagnostics import SushiError
from sushi_lang.internals.report import DiagnosticBuilder, Reporter, Span

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"


def _parameters(function) -> dict:
    return dict(inspect.signature(function).parameters)


def test_the_prose_note_has_no_span_parameter():
    for owner in (DiagnosticBuilder, SushiError):
        assert list(_parameters(owner.note)) == ["self", "message"], owner.__name__


def test_the_relational_note_requires_its_span():
    for owner in (DiagnosticBuilder, SushiError):
        span = _parameters(owner.note_at)["span"]
        assert span.default is inspect.Parameter.empty, owner.__name__
        assert span.annotation == "Span", owner.__name__


def _note_calls():
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("note", "note_at")):
                yield path.relative_to(PACKAGE.parent), node


def test_every_call_site_uses_the_entry_its_arguments_name():
    wrong = []
    for path, call in _note_calls():
        count = len(call.args) + len(call.keywords)
        if call.func.attr == "note" and (count != 1 or call.keywords):
            wrong.append(f"{path}:{call.lineno}: .note() with a location -- use .note_at()")
        if call.func.attr == "note_at" and count < 2:
            wrong.append(f"{path}:{call.lineno}: .note_at() with no span -- use .note()")
    assert not wrong, "\n".join(wrong)


def test_the_census_sees_both_entries():
    """Always-fires control: a scan that finds nothing proves nothing."""
    kinds = {call.func.attr for _path, call in _note_calls()}
    assert kinds == {"note", "note_at"}


def test_a_relational_note_renders_its_location():
    reporter = Reporter(source="fn a() i32:\nfn a() i32:\n", filename="main.sushi")
    reporter.error_with("CE0101", "first", Span(2, 4, 2, 5)) \
        .note_at("first defined here", Span(1, 4, 1, 5)).emit()
    diagnostic, = reporter.items
    note, = diagnostic.sub
    assert note.span == Span(1, 4, 1, 5)


def test_a_prose_note_carries_no_location():
    reporter = Reporter(source="x\n", filename="main.sushi")
    reporter.error_with("CE0101", "first", Span(1, 1, 1, 2)).note("a fact").emit()
    diagnostic, = reporter.items
    note, = diagnostic.sub
    assert note.span is None and note.filename is None


def test_an_exception_note_keeps_its_entry():
    exc = SushiError("CE6013").note_at("opens here", Span(1, 1, 1, 4)).note("prose")
    assert exc.notes == [("opens here", Span(1, 1, 1, 4), None), ("prose", None, None)]
