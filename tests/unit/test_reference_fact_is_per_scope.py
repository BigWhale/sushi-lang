"""The answer to "is this name a reference" belongs to the open scope, not to the name (#941).

The scope manager records a local's semantic type with its slot and drops both when the
scope pops. A flat table keyed by the name alone has no scope, so no backend module may
keep one, read one or write one (#958).
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"
_FLAT_TABLE = "variable_types"


def _mentions(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _FLAT_TABLE:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == _FLAT_TABLE:
            lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value == _FLAT_TABLE:
            lines.append(node.lineno)
    return sorted(lines)


def _backend_sources() -> list[Path]:
    return sorted(_BACKEND.rglob("*.py"))


def test_the_scan_reads_the_whole_backend():
    """A scan over no file answers the same empty list a clean backend does."""
    names = {p.relative_to(_BACKEND).as_posix() for p in _backend_sources()}
    assert {"codegen_llvm.py", "expressions/names.py", "statements/matching.py"} <= names


def test_the_scan_sees_every_spelling():
    """An attribute, a bare name and a `getattr` string each count as a use."""
    probe = ast.parse(
        "codegen.variable_types[n] = t\n"
        "variable_types = {}\n"
        "getattr(codegen, 'variable_types')\n"
    )
    assert _mentions(probe) == [1, 2, 3]


def test_no_backend_module_keeps_a_flat_name_table():
    """Every reader asks `codegen.memory`; a second, unscoped home is refused."""
    offenders = []
    for path in _backend_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line in _mentions(tree):
            offenders.append(f"{path.relative_to(_BACKEND).as_posix()}:{line}")
    assert offenders == [], f"the flat `{_FLAT_TABLE}` table is used at: {offenders}"
