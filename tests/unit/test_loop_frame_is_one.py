"""Every backend loop builds its frame through `loop_frame` (#839).

A loop frame is a contract with `emit_break` and `emit_continue`: the loop-stack entry
holds the continue target, the break target and the scope depth above which an early
exit cleans up. Five emitters wrote that entry, the scope push, the two pops and the
back edge by hand, so five copies could disagree. `loop_frame` in
`statements/loops.py` is the one place that writes them now, and the statement
emitters share one `_emit_block`.
"""
from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"


def _sites(predicate) -> list[tuple[str, str]]:
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if predicate(node):
                    found.append((path.relative_to(BACKEND).as_posix(), fn.name))
    return sorted(set(found))


def _is_loop_stack_append(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "loop_stack")


def test_one_site_pushes_a_loop_frame():
    assert _sites(_is_loop_stack_append) == [("statements/loops.py", "loop_frame")]


def test_the_statement_emitters_share_one_emit_block():
    defs = [path.relative_to(BACKEND).as_posix()
            for path in sorted(BACKEND.rglob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef) and node.name == "_emit_block"]
    assert defs == ["statements/loops.py"], defs


def test_the_detector_sees_a_hand_written_frame():
    """The control: a detector that finds nothing would pass the first row for free."""
    tree = ast.parse("def emit(codegen, a, b):\n    codegen.loop_stack.append((a, b, 0))\n")
    assert sum(_is_loop_stack_append(n) for n in ast.walk(tree)) == 1
