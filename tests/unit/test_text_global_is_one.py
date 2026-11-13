"""A private string global of the IR is made by `text_global`, `backend/string_constants.py`.

This gate reads the Python source of `sushi_lang/`. It refuses a function, other than
`text_global`, that constructs an `ir.GlobalVariable(...)` and also makes the bytes of a
text (`bytearray(...)` or `.encode(...)`): that is a hand-made string global. A global
whose initializer comes from somewhere else (a constant value, an external handle) is not
matched. The gate does not run the compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

SEAM = ("backend/string_constants.py", "text_global")


def _is_global_variable(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name == "GlobalVariable"


def _makes_bytes(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "bytearray"
    return isinstance(func, ast.Attribute) and func.attr == "encode"


def hand_made_text_globals(source: str, path: str) -> list[str]:
    """Each function of `source` that builds a string global by hand, as `path:line name`."""
    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (path, fn.name) == SEAM:
            continue
        nodes = list(ast.walk(fn))
        if any(_is_global_variable(n) for n in nodes) and any(_makes_bytes(n) for n in nodes):
            found.append(f"{path}:{fn.lineno} {fn.name}")
    return found


def test_no_hand_made_string_global() -> None:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        found += hand_made_text_globals(path.read_text(encoding="utf-8"), relative)
    assert found == [], ("make a private string global with `text_global`, "
                         f"backend/string_constants.py: {found}")


def test_the_gate_sees_a_hand_made_global() -> None:
    planted = (
        "def make(module, text):\n"
        "    data = bytearray(text.encode('utf-8'))\n"
        "    ty = ir.ArrayType(ir.IntType(8), len(data))\n"
        "    return ir.GlobalVariable(module, ty, name='x')\n"
    )
    assert hand_made_text_globals(planted, "backend/planted.py") == ["backend/planted.py:1 make"]
