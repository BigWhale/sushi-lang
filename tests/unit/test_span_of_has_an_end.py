"""A span that the AST builder reads from the parser always has an end.

Lark gives no end to the tree of a block at the end of a file with no newline. The
renderer compares the end line of a span, so a span with no end stopped the compiler with
a Python traceback when a diagnostic stood at such a block (an internal error of a
finished body names the span of its block). `span_of` (`internals/report.py`) gives a tree
with no end the end of its start, as it does for a token. The test reads plain data.
"""
from __future__ import annotations

from types import SimpleNamespace

from sushi_lang.internals.report import Span, span_of


def test_a_tree_with_no_end_has_a_point_span() -> None:
    meta = SimpleNamespace(line=1, column=15, end_line=None, end_column=None)
    assert span_of(SimpleNamespace(meta=meta)) == Span(1, 15, 1, 15)


def test_a_tree_with_an_end_keeps_it() -> None:
    meta = SimpleNamespace(line=1, column=15, end_line=3, end_column=9)
    assert span_of(SimpleNamespace(meta=meta)) == Span(1, 15, 3, 9)
