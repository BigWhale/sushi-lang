"""The source text of an expression, read from its span.

A diagnostic names an expression by the text that the user wrote. A node of the AST and a
node of SHIR each hold the span of what they stand for, so the text comes from the span
and the source, and no pass spells a node again from its fields. The AST builder and the
borrow pass both read it.
"""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals.report import Span


def source_text(source: Optional[str], span: Optional[Span]) -> Optional[str]:
    """The text that `span` covers in `source`, or None when the span is outside it.

    A span counts its lines and its columns from 1, and its end column is not in it.
    """
    if source is None or span is None:
        return None
    lines = source.splitlines()
    if not (1 <= span.line <= span.end_line <= len(lines)):
        return None
    first, last = lines[span.line - 1], lines[span.end_line - 1]
    if not (1 <= span.col <= len(first) + 1 and 1 <= span.end_col <= len(last) + 1):
        return None
    if span.line == span.end_line:
        return first[span.col - 1:span.end_col - 1] if span.col <= span.end_col else None
    middle = lines[span.line:span.end_line - 1]
    return "\n".join([first[span.col - 1:], *middle, last[:span.end_col - 1]])
