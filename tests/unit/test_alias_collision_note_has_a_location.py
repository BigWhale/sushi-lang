"""CE3013's relational note carries a location, or it is not a note (#714).

CLAUDE.md's diagnostic ladder calls a relational error rendered with a single location
a regression. `_names_declared_by` answers with a span per name, and the span is
`Optional`: a declaration the source did not write has none to give. The map was typed
as if it could not happen, and `DiagnosticBuilder.note` takes a `None` span without a
word, so the pair would have rendered `= note: this unit declares the name here` with
nothing under it.

No source reproduces it today -- the `namespaces` pass runs before `instantiate`, and a
library-injected declaration carries its own span -- so the span-less map is built here
by hand. The rule is the same one the `unsafe external` arm beside it already follows:
with no location to point at, the second half is a `help`.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Reporter, Span
from sushi_lang.semantics.ast import UseStatement
from sushi_lang.semantics.namespaces import NamespaceTable
from sushi_lang.semantics.passes.namespaces import _reject_alias_collision

ALIAS_SPAN = Span(1, 19, 1, 24)


def _refuse(declared_at):
    """One alias against one declaration of the same name; the diagnostic it makes."""
    reporter = Reporter(source='use "thing" as Thing\n', filename="main")
    use_stmt = UseStatement(loc=ALIAS_SPAN, path="thing", alias="Thing",
                            alias_span=ALIAS_SPAN)
    refused = _reject_alias_collision(reporter, NamespaceTable(),
                                      {"Thing": declared_at}, use_stmt, "Thing")
    assert refused
    diagnostic, = reporter.items
    assert diagnostic.code == "CE3013"
    return diagnostic


def test_a_declaration_with_a_span_keeps_the_note():
    """The ordinary case: the note points at what took the name first."""
    declared_at = Span(3, 8, 3, 13)
    diagnostic = _refuse(declared_at)
    assert [(sub.kind, sub.span) for sub in diagnostic.sub] == [("note", declared_at)]


def test_a_declaration_with_no_span_answers_with_a_help():
    """The hardening case: no location, so no note that pretends to have one."""
    diagnostic = _refuse(None)
    assert [sub.kind for sub in diagnostic.sub] == ["help"]


@pytest.mark.parametrize("declared_at", [Span(3, 8, 3, 13), None])
def test_no_note_is_ever_rendered_without_a_location(declared_at):
    """The invariant both cases serve, stated once."""
    diagnostic = _refuse(declared_at)
    assert all(sub.span is not None for sub in diagnostic.sub if sub.kind == "note")
