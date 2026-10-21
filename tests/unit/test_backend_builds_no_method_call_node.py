"""The backend builds no call node.

A `MethodCall` rebuilt from an incoming node carries no typecheck stamp, so the first
emitter that reads one (for example `callee_param_modes`) reads `None`. The backend AST
scan of docs/design/ir.md 6.4 replaces this gate at P6.last.
"""
from __future__ import annotations

import ast
from pathlib import Path

from sushi_lang.backend import codegen_llvm

BACKEND = Path(codegen_llvm.__file__).resolve().parent

# No backend function builds a call node. The hash sites that did (#855) call
# `emit_value_hash` and `emit_key_hash_i32` now, so the list is empty and stays empty.
KNOWN_NODE_BUILDERS: set[tuple[str, str]] = set()


def _node_builders(source: str) -> list[str]:
    """Every function that calls `MethodCall(...)` or `DotCall(...)`, by name."""
    found: list[str] = []

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            elif isinstance(child, ast.Call):
                func = child.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called in ("MethodCall", "DotCall"):
                    found.append(owner)
            walk(child, name)

    walk(ast.parse(source), "<module>")
    return found


def _backend_node_builders() -> set[tuple[str, str]]:
    return {
        (path.relative_to(BACKEND).as_posix(), owner)
        for path in sorted(BACKEND.rglob("*.py"))
        for owner in _node_builders(path.read_text(encoding="utf-8"))
    }


def test_no_backend_module_builds_a_call_node():
    extra = _backend_node_builders() - KNOWN_NODE_BUILDERS
    assert not extra, sorted(extra)


def test_the_known_node_builders_only_shrink():
    gone = KNOWN_NODE_BUILDERS - _backend_node_builders()
    assert not gone, f"delete these rows from KNOWN_NODE_BUILDERS: {sorted(gone)}"


def test_the_scan_sees_a_multi_line_build():
    source = """
def rebuild(expr):
    return MethodCall(
        receiver=expr.receiver, method=expr.method, args=[], loc=expr.loc)

def other(expr):
    return ast_mod.DotCall(receiver=expr, method="m", args=[], loc=(0, 0))
"""
    assert _node_builders(source) == ["rebuild", "other"]
