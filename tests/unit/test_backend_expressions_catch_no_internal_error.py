"""No module under `backend/expressions/` catches `InternalCompilerError`.

#823: four callers caught the internal error of `infer_struct_type` to mean "not a
struct". The catch also hid a real compiler fault. The AST expression layer is gone
(P2.13); the rule holds for what stays there: the value halves, the memory helpers of
the families and the stdlib call table.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend" / "expressions"


def _caught_names(handler: ast.ExceptHandler) -> list[str]:
    node = handler.type
    items = node.elts if isinstance(node, ast.Tuple) else [node] if node else []
    names = []
    for item in items:
        if isinstance(item, ast.Name):
            names.append(item.id)
        elif isinstance(item, ast.Attribute):
            names.append(item.attr)
    return names


def test_no_module_catches_an_internal_error():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and "InternalCompilerError" in _caught_names(node):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "an `except InternalCompilerError` under backend/expressions/ reads a compiler "
        "fault as an answer; answer None for \"not of this kind\": " + ", ".join(offenders))


def test_the_scan_sees_the_tree():
    assert len(list(ROOT.rglob("*.py"))) > 10
