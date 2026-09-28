"""Two readers that #791 moves onto their seams.

Row 7: `TypeResolver.resolve_type_args` resolves each type argument through
`type_walk.map_named_types`, the one rebuilding walk. A descent of its own entered two
kinds (an array element and a `GenericTypeRef`'s arguments) and missed a function type's
arms, a reference, a pointer and an iterator.

Row 1 residual: the backend asks "does this type implement `Drop`" through the set
`drops_of(codegen)` returns, never through a per-type `implements(name, "Drop")`.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
RESOLUTION = ROOT / "semantics" / "type_resolution.py"
HAND_KINDS = {"ArrayType", "DynamicArrayType", "GenericTypeRef"}


def _resolve_type_args() -> ast.FunctionDef:
    tree = ast.parse(RESOLUTION.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TypeResolver":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "resolve_type_args":
                    return item
    raise AssertionError("TypeResolver.resolve_type_args not found")


def _names(fn: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name)
    return found


def test_resolve_type_args_calls_the_walk():
    assert "map_named_types" in _names(_resolve_type_args())


def test_resolve_type_args_has_no_descent_of_its_own():
    names = _names(_resolve_type_args())
    assert not names & HAND_KINDS, sorted(names & HAND_KINDS)
    assert "resolve_type_args" not in names


def _drop_reads_in(tree: ast.AST) -> list[int]:
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "implements"
        and any(isinstance(a, ast.Constant) and a.value == "Drop" for a in node.args)
    ]


def _per_type_drop_reads() -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted(ROOT.rglob("*.py"))
        for line in _drop_reads_in(ast.parse(path.read_text()))
    ]


def test_the_scan_sees_a_per_type_drop_read():
    """The control: the scan finds the spelling in a probe."""
    assert _drop_reads_in(ast.parse('codegen.perk_impl_table.implements(name, "Drop")')) == [1]


def test_no_per_type_drop_read():
    assert _per_type_drop_reads() == [], _per_type_drop_reads()
