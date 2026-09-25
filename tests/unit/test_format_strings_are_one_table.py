"""A console format string has one source and one cache (#881).

`FORMAT_STRINGS` (`backend/runtime/constants.py`) gives each name its one text. The
formatting manager keeps the emitted globals in one dict keyed by that name, and
`_get_format_string(name)` takes the name alone: a second text argument counted only on a
cache miss, so a call site could write a format that the output did not use.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sushi_lang.backend.runtime.formatting import FormattingOperations

FORMATTING = (Path(__file__).resolve().parents[2]
              / "sushi_lang" / "backend" / "runtime" / "formatting.py")


def _tree() -> ast.Module:
    return ast.parse(FORMATTING.read_text(encoding="utf-8"))


def test_get_format_string_takes_the_name_only():
    params = list(inspect.signature(FormattingOperations._get_format_string).parameters)
    assert params == ["self", "name"]


def test_no_format_slot_attribute():
    slots = sorted({node.attr for node in ast.walk(_tree())
                    if isinstance(node, ast.Attribute) and node.attr.startswith("fmt_")})
    assert slots == []


def test_no_attribute_is_found_by_a_computed_name():
    hits = [node.lineno for node in ast.walk(_tree())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "setattr")]
    assert hits == []


def test_no_format_text_is_written_outside_the_table():
    tree = _tree()
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)}
    texts = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and "%" in node.value and id(node) not in docstrings]
    assert texts == []
