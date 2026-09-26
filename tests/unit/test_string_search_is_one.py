"""The string search is one emitter: `_emit_search` in `strings/methods/search.py` (#909).

`contains`, `find`, `find_last` and `count` differ only in their answer and in the scan
direction, so each is a caller of the one skeleton. A function in `search.py` that builds a
scan loop of its own -- an `outer_loop_cond` or `inner_loop_cond` block -- is a copy of the
search, and a fault in a copy must then be fixed more than once.
"""
from __future__ import annotations

import ast
from pathlib import Path

import llvmlite.ir as ir

from sushi_lang.sushi_stdlib.src.collections.strings.methods import search

SEARCH = Path(search.__file__)
SKELETON = "_emit_search"
LOOP_BLOCKS = {"outer_loop_cond", "inner_loop_cond"}
PUBLIC = {
    "emit_string_contains",
    "emit_string_find",
    "emit_string_find_last",
    "emit_string_count",
}


def _functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(SEARCH.read_text())
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _names_a_loop_block(fn: ast.FunctionDef) -> bool:
    return any(
        isinstance(n, ast.Constant) and n.value in LOOP_BLOCKS for n in ast.walk(fn)
    )


def _callees(fn: ast.FunctionDef) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _reaches_skeleton(name: str, functions: dict[str, ast.FunctionDef], seen: set[str]) -> bool:
    if name == SKELETON:
        return True
    if name in seen or name not in functions:
        return False
    seen.add(name)
    return any(_reaches_skeleton(c, functions, seen) for c in _callees(functions[name]))


def test_only_the_skeleton_builds_a_scan_loop():
    functions = _functions()
    builders = {name for name, fn in functions.items() if _names_a_loop_block(fn)}
    assert builders == {SKELETON}


def test_every_search_method_calls_the_skeleton():
    functions = _functions()
    missing = {name for name in PUBLIC if not _reaches_skeleton(name, functions, set())}
    assert not missing


def test_the_gate_sees_a_copy():
    copy = ast.parse('def emit_copy(m):\n    m.append_basic_block("outer_loop_cond")\n').body[0]
    assert isinstance(copy, ast.FunctionDef)
    assert _names_a_loop_block(copy)


def test_every_search_method_emits_the_same_scan():
    module = ir.Module(name="search_gate")
    for name in sorted(PUBLIC):
        func = getattr(search, name)(module)
        blocks = {b.name for b in func.blocks}
        assert LOOP_BLOCKS | {"empty_needle_check", "size_check", "inner_loop_mismatch"} <= blocks
