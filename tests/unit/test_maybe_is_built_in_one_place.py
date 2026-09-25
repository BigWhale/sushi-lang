"""The backend builds a `Maybe@(T)` only through `generics/maybe.py` (#872).

`emit_maybe_some` and `emit_maybe_none` read the variant index from the enum. A caller
that finds the enum with `ensure_maybe_type_exists` and builds the value itself writes
the tag as a number, and that number drifts from the variant order without a sign. So
`ensure_maybe_type_exists` is called in `generics/maybe.py` and nowhere else.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

MAYBE_MODULE = "generics/maybe.py"


def _callers(source: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called == "ensure_maybe_type_exists":
                lines.append(node.lineno)
    return lines


def test_only_the_maybe_module_finds_the_maybe_enum():
    outside = [
        f"{path.relative_to(BACKEND).as_posix()}:{line}"
        for path in sorted(BACKEND.rglob("*.py"))
        if path.relative_to(BACKEND).as_posix() != MAYBE_MODULE
        for line in _callers(path.read_text(encoding="utf-8"))
    ]
    assert not outside, (
        "build a Maybe with emit_maybe_some / emit_maybe_none (generics/maybe.py):\n"
        + "\n".join(outside))


def test_the_scan_sees_a_call():
    source = """
def f(codegen, t):
    from x import ensure_maybe_type_exists
    return maybe.ensure_maybe_type_exists(codegen, t), ensure_maybe_type_exists(codegen, t)
"""
    assert _callers(source) == [4, 4]


def test_the_maybe_module_still_holds_the_seam():
    source = (BACKEND / MAYBE_MODULE).read_text(encoding="utf-8")
    assert _callers(source), "the seam moved: update MAYBE_MODULE"
