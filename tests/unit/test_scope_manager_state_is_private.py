"""Only `ScopeManager` touches its own state (#835).

The scope manager owns the locals, their types and the cleanup registries. A module that
writes `_locals` or `_scope_vars` by hand is a second copy of the tracking body, and it
goes out of step with the manager when the manager changes. So no backend module outside
`memory/scopes.py` reads an attribute of `codegen.memory` whose name starts with `_`.
The public surface is `depth`, `track_local`, `create_local` and the registration methods.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"


def _private_memory_reads(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    return [(node.lineno, node.attr) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr.startswith("_")
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "memory"]


def test_no_backend_module_reads_the_scope_managers_private_state():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel == "memory/scopes.py":
            continue
        offenders += [f"{rel}:{line} reads memory.{attr}"
                      for line, attr in _private_memory_reads(path.read_text())]
    assert not offenders, (
        "the scope manager's state is its own; use its public methods:\n"
        + "\n".join(offenders))


def test_the_gate_sees_a_private_read():
    source = "codegen.memory._scope_depth\nself.codegen.memory._locals[n]\ncodegen.memory.depth\n"
    assert sorted(attr for _, attr in _private_memory_reads(source)) == ["_locals", "_scope_depth"]
