"""An expression operator is named in `semantics/operators.py`, and only there.

This gate reads the Python source of `sushi_lang/`. It refuses a comparison of an `.op`, or
of a name `op`, with a written string or with a tuple, list or set of written strings
(`node.op == "neg"`, `op in ("/", "%")`). Such a test names an operator a second time; it
uses a name of `operators.py` (`NEG_OP`, `DIVISION_OPS`). The gate does not run the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

ALLOWED = {
    "semantics/operators.py": "the home of the names",
    "internals/semver.py": "a version operator of a dependency, not an expression operator",
}


def _is_op(node: ast.AST) -> bool:
    return ((isinstance(node, ast.Attribute) and node.attr == "op")
            or (isinstance(node, ast.Name) and node.id == "op"))


def _is_written(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts) and all(_is_written(elt) for elt in node.elts)
    return False


def written_operator_tests(source: str, path: str) -> list[str]:
    """Each comparison of an operator with a written string in `source`, as `path:line`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        if any(_is_op(side) for side in sides) and any(_is_written(side) for side in sides):
            found.append(f"{path}:{node.lineno}")
    return found


def test_no_written_operator_outside_operators_py() -> None:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOWED:
            continue
        found += written_operator_tests(path.read_text(encoding="utf-8"), relative)
    assert found == [], f"use a name of semantics/operators.py: {found}"


def test_the_gate_sees_a_written_operator() -> None:
    planted = ("if node.op == 'neg':\n    pass\n"
               "if op in ('/', '%'):\n    pass\n"
               "if node.op in DIVISION_OPS:\n    pass\n")
    assert written_operator_tests(planted, "x.py") == ["x.py:1", "x.py:3"]
