"""The source text of an expression comes from its span (H4).

A diagnostic names an expression by the text that the user wrote. A node holds the span of
what it stands for, so ONE function reads the text from the span
(`semantics/source_text.py`), and no pass spells a node again from its fields: the borrow
pass on SHIR reads it. The place of a tuple rebind (CE6109) is no text: the AST builder
compares the steps of the place. These tests call the function over a text; they do not
run the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.source_text import source_text

SOURCE = Path(__file__).resolve().parents[2] / "sushi_lang"

_TEXT = "fn main() i32:\n    let i32 y = (x + 2) * f(x,  3)\n    return y\n"


def test_a_span_on_one_line_reads_its_columns():
    assert source_text(_TEXT, Span(2, 17, 2, 35)) == "(x + 2) * f(x,  3)"
    assert source_text(_TEXT, Span(2, 18, 2, 23)) == "x + 2"


def test_a_span_over_lines_reads_each_line():
    assert source_text(_TEXT, Span(2, 27, 3, 11)) == "f(x,  3)\n    return"


@pytest.mark.parametrize("span", [None, Span(9, 1, 9, 2), Span(2, 40, 2, 44)],
                         ids=["no_span", "past_the_last_line", "past_the_end_of_the_line"])
def test_a_span_outside_the_text_has_no_text(span):
    assert source_text(_TEXT, span) is None


def test_no_text_has_no_text():
    assert source_text(None, Span(1, 1, 1, 3)) is None


def test_no_module_spells_an_expression_from_its_fields():
    """`expr_to_string` was the second spelling of the source text: it is gone."""
    found = [path.relative_to(SOURCE).as_posix() for path in sorted(SOURCE.rglob("*.py"))
             if any(isinstance(node, (ast.FunctionDef, ast.Name, ast.Attribute, ast.alias))
                    and "expr_to_string" in (getattr(node, "name", None),
                                             getattr(node, "id", None),
                                             getattr(node, "attr", None))
                    for node in ast.walk(ast.parse(path.read_text("utf-8"))))]
    assert not found, f"these modules still spell an expression from its fields: {found}"
