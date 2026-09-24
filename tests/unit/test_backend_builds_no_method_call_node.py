"""The backend hands the incoming call node to an emitter and builds no copy of it.

A `MethodCall` rebuilt from the incoming node carries no typecheck stamp, so the
first emitter that reads one (for example `callee_param_modes`) reads `None`. The
emitters read `.receiver`, `.method`, `.args` and `.loc`, which a `DotCall` has too,
so the node that arrives is the node that goes down.

The two container emitters (`HashMap@(K, V)`, `List@(T)`) are one body with one row
per container.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sushi_lang.backend.expressions.calls import generics

BACKEND = Path(generics.__file__).resolve().parents[2]

# The element-hash sites build a fake node for the derived hash emitter (#855). This list
# may only get shorter.
KNOWN_NODE_BUILDERS = {
    ("generics/hashmap/methods/core.py", "emit_hashmap_get"),
    ("generics/hashmap/methods/core.py", "emit_hashmap_contains_key"),
    ("generics/hashmap/methods/mutations.py", "emit_hashmap_insert"),
    ("generics/hashmap/methods/mutations.py", "emit_hashmap_remove"),
    ("generics/hashmap/methods/mutations.py", "emit_hashmap_resize_to_capacity"),
    ("types/arrays/methods/hashing.py", "emit_element_hash"),
    ("types/enums.py", "_emit_associated_value_hash"),
    ("types/structs.py", "_emit_field_hash"),
}


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


def test_no_backend_module_writes_a_stamp_onto_a_call_node():
    source = inspect.getsource(generics)
    assert ".resolved_enum_type = " not in source


def test_the_two_container_emitters_share_one_body():
    assert hasattr(generics, "_try_emit_container_method")
    for name in ("try_emit_hashmap_method", "try_emit_list_method"):
        body = inspect.getsource(getattr(generics, name))
        assert "_try_emit_container_method" in body, name
        assert "infer_semantic_type" not in body, name
        assert "emit_receiver_as_pointer" not in body, name
