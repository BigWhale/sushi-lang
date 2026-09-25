"""A backend invariant is a registered internal error, never an `assert` (#880).

`python -O` removes an `assert` statement. A broken invariant then fails later, at a less
clear place, where a structured CE0xxx diagnostic must stop the build. So an invariant
under `sushi_lang/backend/` is `raise_internal_error(<code>)`, and an `assert` is for
tests only.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"


def _asserts(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_the_scan_sees_an_assert():
    tree = ast.parse("def f(x):\n    assert x\n")
    assert any(isinstance(node, ast.Assert) for node in ast.walk(tree))


def test_backend_has_no_assert():
    hits = [f"{path.relative_to(BACKEND)}:{line}"
            for path in sorted(BACKEND.rglob("*.py")) for line in _asserts(path)]
    assert hits == []
