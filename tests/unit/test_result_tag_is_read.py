"""A stdlib generator reads a variant tag from the interned enum (#932).

`results.result_tag(variant)` is the one reader of a `Result` tag. This gate scans
`sushi_lang/sushi_stdlib/src/**` and refuses a literal integer written at field 0 of a
value: an `insert_value(_, ir.Constant(_, <int>), 0)`, and a `store(ir.Constant(_, <int>),
p)` where `p` is a `gep(_, [<0>, <0>])` in the same function.

`MAYBE_LITERALS` is an empty ratchet: no generator writes a `Maybe` tag as a number.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "sushi_lang" / "sushi_stdlib" / "src"

MAYBE_LITERALS: dict[str, int] = {}


def _is_int_constant(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Constant"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and type(node.args[1].value) is int)


def _is_zero(node: ast.AST, zeros: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in zeros
    return _is_int_constant(node) and node.args[1].value == 0  # type: ignore[attr-defined]


def _is_method(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name)


def _is_field_zero_gep(node: ast.AST, zeros: set[str]) -> bool:
    if not _is_method(node, "gep") or len(node.args) < 2:  # type: ignore[attr-defined]
        return False
    indices = node.args[1]  # type: ignore[attr-defined]
    return (isinstance(indices, ast.List) and len(indices.elts) == 2
            and all(_is_zero(e, zeros) for e in indices.elts))


def _literal_tags(tree: ast.AST) -> int:
    count = 0
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        zeros: set[str] = set()
        tag_slots: set[str] = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                if _is_zero(node.value, zeros) and _is_int_constant(node.value):
                    zeros.add(node.targets[0].id)
                elif _is_field_zero_gep(node.value, zeros):
                    tag_slots.add(node.targets[0].id)
        for node in ast.walk(func):
            if _is_method(node, "insert_value") and len(node.args) >= 3:  # type: ignore[attr-defined]
                value, index = node.args[1], node.args[2]  # type: ignore[attr-defined]
                if _is_int_constant(value) and isinstance(index, ast.Constant) and index.value == 0:
                    count += 1
            elif _is_method(node, "store") and len(node.args) == 2:  # type: ignore[attr-defined]
                value, slot = node.args  # type: ignore[attr-defined]
                if _is_int_constant(value) and (
                        (isinstance(slot, ast.Name) and slot.id in tag_slots)
                        or _is_field_zero_gep(slot, zeros)):
                    count += 1
    return count


def _counts() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(SRC.rglob("*.py")):
        count = _literal_tags(ast.parse(path.read_text()))
        if count:
            found[path.relative_to(SRC).as_posix()] = count
    return found


def test_the_gate_sees_a_literal_tag():
    probe = ast.parse(
        "def f(b, r, i32):\n"
        "    z = ir.Constant(i32, 0)\n"
        "    b.insert_value(r, ir.Constant(i32, 1), 0)\n"
        "    p = b.gep(r, [z, ir.Constant(i32, 0)])\n"
        "    b.store(ir.Constant(i32, 0), p)\n"
    )
    assert _literal_tags(probe) == 2


def test_no_generator_writes_a_literal_tag():
    found = _counts()
    grown = {path: n for path, n in found.items() if n > MAYBE_LITERALS.get(path, 0)}
    assert not grown, grown
    shrunk = {path: n for path, n in MAYBE_LITERALS.items() if found.get(path, 0) < n}
    assert not shrunk, f"lower MAYBE_LITERALS to the measured counts: {shrunk} -> {found}"
