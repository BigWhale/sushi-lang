"""A generic function's body is one source, so it answers one report (#648).

Every monomorphized instance carries the TEMPLATE's spans, and the per-unit passes walk
the instances as ordinary functions -- correctly, because a per-instance truth can only be
found there. What must not follow is the count: one fault in the body used to answer once
per instantiation, at one caret.

The rule is the one `_check_monomorphized_extensions` already applies to a generic-target
extension: identity is the kind, the code, the MESSAGE, the file and the span, so a
finding that genuinely differs by type argument keeps its own message and is still told.
"""
from __future__ import annotations

from sushi_lang.internals.report import Diagnostic, Reporter, Span




























def test_a_reporter_keeps_a_repeat_by_default():
    """Collapsing is scoped to an instance body: nothing else loses a second copy."""
    reporter = Reporter(filename="a.sushi")
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    assert len(reporter.items) == 2


def test_a_reporter_collapses_a_repeat_while_reading_an_instance_body():
    reporter = Reporter(filename="a.sushi")
    reporter.collapse_repeats = True
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.error("CE1", "other", Span(1, 1, 1, 1))
    assert [d.message for d in reporter.items] == ["same", "other"]


def test_a_collapsed_reporter_keeps_what_was_already_told():
    """A fault the TEMPLATE raised is the same fault, so the instance adds nothing."""
    reporter = Reporter(filename="a.sushi")
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    reporter.collapse_repeats = True
    reporter.error("CE1", "same", Span(1, 1, 1, 1))
    assert len(reporter.items) == 1


def test_the_identity_is_the_kind_code_message_file_and_span():
    from sushi_lang.internals.report import diagnostic_identity

    one = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="a.sushi")
    same = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="a.sushi")
    other_file = Diagnostic("error", "CE1", "m", Span(1, 2, 1, 3), filename="b.sushi")
    assert diagnostic_identity(one) == diagnostic_identity(same)
    assert diagnostic_identity(one) != diagnostic_identity(other_file)
