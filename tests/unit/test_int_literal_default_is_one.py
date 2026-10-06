"""The default type of an integer literal is answered in ONE place.

An integer literal that no position types is an `i32`, and a byte literal (`a'x'`) is a
`u8`. `int_literal_default` in `semantics/passes/types/inference.py` gives that answer.
When a second place answers `i32` by itself, the typecheck pass, the generic inference
and the backend can give three different types to one byte literal.

The scan refuses four shapes in `semantics/` and `backend/`:
- `<x>.resolved_type or BuiltinType.I32`;
- a dict entry `IntLit: BuiltinType.I32`;
- a `return` that names `BuiltinType.I32` in a function that takes an `IntLit` parameter;
- a `return` that names `BuiltinType.I32` in the body of an `isinstance(<x>, IntLit)` test.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("sushi_lang/semantics", "sushi_lang/backend")
SEAM = ("sushi_lang/semantics/passes/types/inference.py", "int_literal_default")


def _is_i32(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "I32"
            and isinstance(node.value, ast.Name) and node.value.id == "BuiltinType")


def _names_i32(node: ast.AST) -> bool:
    return any(_is_i32(n) for n in ast.walk(node))


def _is_intlit(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "IntLit"
    if isinstance(node, ast.Attribute):
        return node.attr == "IntLit"
    if isinstance(node, ast.Constant):
        return node.value == "IntLit"
    return False


def _tests_intlit(test: ast.AST) -> bool:
    for n in ast.walk(test):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "isinstance" and len(n.args) == 2):
            kinds = n.args[1]
            items = kinds.elts if isinstance(kinds, ast.Tuple) else [kinds]
            if any(_is_intlit(k) for k in items):
                return True
    return False


def _returns_i32(body: list[ast.stmt]) -> list[int]:
    return [n.lineno for stmt in body for n in ast.walk(stmt)
            if isinstance(n, ast.Return) and n.value is not None and _names_i32(n.value)]


def default_readers(source: str, skip_function: str | None = None) -> list[int]:
    """The line of every place in one module that answers i32 for an integer literal."""
    tree = ast.parse(source)
    skipped: set[int] = set()
    if skip_function is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == skip_function:
                skipped.update(id(n) for n in ast.walk(node))
    hits: set[int] = set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            values = node.values
            if (_is_i32(values[-1]) and any(
                    isinstance(v, ast.Attribute) and v.attr == "resolved_type"
                    for v in values[:-1])):
                hits.add(node.lineno)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if key is not None and _is_intlit(key) and _is_i32(value):
                    hits.add(key.lineno)
        elif isinstance(node, ast.FunctionDef):
            params = node.args.args + node.args.kwonlyargs + node.args.posonlyargs
            if any(p.annotation is not None and _is_intlit(p.annotation) for p in params):
                hits.update(_returns_i32(node.body))
        elif isinstance(node, ast.If) and _tests_intlit(node.test):
            hits.update(_returns_i32(node.body))
    return sorted(hits)


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            skip = SEAM[1] if rel == SEAM[0] else None
            lines = default_readers(path.read_text(), skip)
            if lines:
                found[rel] = lines
    return found


def test_int_literal_default_is_one():
    found = _scan()
    assert not found, (
        "These places answer the default type of an integer literal by themselves. "
        "Call int_literal_default (semantics/passes/types/inference.py) instead: "
        f"{found}")


def test_the_seam_exists():
    from sushi_lang.semantics.ast import IntLit
    from sushi_lang.semantics.passes.types.inference import int_literal_default
    from sushi_lang.semantics.typesys import BuiltinType

    assert int_literal_default(IntLit(value=47)) == BuiltinType.I32
    assert int_literal_default(IntLit(value=47, byte_spelling="a'/'")) == BuiltinType.U8


def test_the_scan_sees_each_shape():
    probes = {
        "or": "def f(e):\n    return e.resolved_type or BuiltinType.I32\n",
        "dict": "T = {IntLit: BuiltinType.I32}\n",
        "param": "def f(expr: IntLit):\n    return Scalar(expr.value, BuiltinType.I32)\n",
        "isinstance": ("def f(e):\n    if isinstance(e, IntLit):\n"
                       "        return BuiltinType.I32\n"),
    }
    for shape, source in probes.items():
        assert default_readers(source), shape
    seam = ("def int_literal_default(lit):\n"
            "    return lit.resolved_type or BuiltinType.I32\n")
    assert default_readers(seam, "int_literal_default") == []
