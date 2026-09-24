"""`copy_out` is the one deep-clone entry (#822).

`ownership.copy_out` loads a value that arrives as a pointer to its own type, then calls
`emit_value_clone`. A site that calls `emit_value_clone` directly skips the load, and a
borrowed receiver (a `peek` / `poke` parameter, a `let peek` binding, a `foreach(poke ...)`
item) then reaches the clone engine as a pointer: `.clone()` on a borrowed function value
was a CE0000 crash for that reason.

Only the entry (`backend/ownership.py`) and the clone engine itself
(`backend/expressions/memory.py`, which recurses through its own arms) may name
`emit_value_clone`. An import counts as well as a call, so an alias cannot hide one.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

ALLOWED = frozenset({
    SOURCE_ROOT / "backend" / "ownership.py",
    SOURCE_ROOT / "backend" / "expressions" / "memory.py",
})

ENGINE = "emit_value_clone"


def _uses_of_engine(tree: ast.AST) -> list[int]:
    """The line of every call or import that names the clone engine."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == ENGINE for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == ENGINE:
                lines.append(node.lineno)
    return lines


def _scan() -> dict[Path, list[int]]:
    found: dict[Path, list[int]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _uses_of_engine(tree)
        if lines:
            found[path] = lines
    return found


def test_the_scan_sees_a_direct_call():
    """Always-fires control: a direct call and an import are both found."""
    source = (
        "from sushi_lang.backend.expressions.memory import emit_value_clone\n"
        "def f(codegen, v, t):\n"
        "    return emit_value_clone(codegen, v, t)\n"
        "def g(codegen, v, t):\n"
        "    return memory.emit_value_clone(codegen, v, t)\n"
    )
    assert _uses_of_engine(ast.parse(source)) == [1, 3, 5]


def test_the_scan_sees_the_allowed_callers():
    """The scan is not vacuous: both allowed modules name the engine."""
    found = _scan()
    for path in ALLOWED:
        assert path in found, f"{path.relative_to(SOURCE_ROOT)} no longer names {ENGINE}"


def test_only_copy_out_and_the_engine_name_emit_value_clone():
    offenders = [
        f"{path.relative_to(SOURCE_ROOT)}:{line}"
        for path, lines in _scan().items()
        if path not in ALLOWED
        for line in lines
    ]
    assert not offenders, (
        f"{ENGINE} is named outside backend/ownership.py and backend/expressions/memory.py:\n  "
        + "\n  ".join(offenders)
        + "\nCall `ownership.copy_out(codegen, value, type)`. It loads a value that arrives "
        "as a pointer to its own type before it clones, and a direct call skips that (#822)."
    )
