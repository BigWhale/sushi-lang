"""Scope exit destroys the locals in ONE walk, and `ScopeManager` owns it (#821).

Every exit kind -- the fall-through `pop_scope`, a `return` or a `??`, a `break` or a
`continue` -- walks the names of each scope newest first and sends each name to the
registry that holds it. A second sweep per registry is how the order became a property
of the registry and not of the scope, so this gate refuses two things:

1. A module outside `memory/scopes.py` that reads a cleanup registry of the scope
   manager. Only the manager may walk them.
2. A module outside `memory/dynamic_arrays.py` that reads the destructor helpers of the
   array manager, which the walk alone calls through `exit_actions`.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

SCOPE_REGISTRIES = {
    "_struct_cleanup", "_closure_cleanup", "_string_cleanup",
    "_cstr_cleanup", "_closure_temp_cleanup", "_scope_vars",
}
ARRAY_EXIT_HELPERS = {"exit_actions", "drain", "_destroy_array", "_destroy_list", "_destroy_own"}

# Two hand copies of the local-tracking body still write `_scope_vars` directly; the
# scope-manager clean-up moves them behind a method. May only shrink.
SCOPE_VARS_WRITERS = {"functions/helpers.py", "statements/initialization.py"}


def _attribute_reads(path: Path, names: set[str]) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [(node.lineno, node.attr) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in names]


def _modules():
    for path in sorted(BACKEND.rglob("*.py")):
        yield path.relative_to(BACKEND).as_posix(), path


def test_only_the_scope_manager_reads_its_cleanup_registries():
    offenders = []
    for rel, path in _modules():
        if rel == "memory/scopes.py":
            continue
        for line, attr in _attribute_reads(path, SCOPE_REGISTRIES):
            if attr == "_scope_vars" and rel in SCOPE_VARS_WRITERS:
                continue
            offenders.append(f"{rel}:{line} reads {attr}")
    assert not offenders, (
        "scope exit is one walk in ScopeManager; call emit_exit_cleanup instead:\n"
        + "\n".join(offenders))


def test_the_known_scope_vars_writers_still_exist():
    for rel in SCOPE_VARS_WRITERS:
        reads = _attribute_reads(BACKEND / rel, {"_scope_vars"})
        assert reads, f"{rel} no longer writes _scope_vars; remove it from the allowlist"


def test_only_the_walk_calls_the_array_exit_helpers():
    offenders = []
    for rel, path in _modules():
        if rel in ("memory/scopes.py", "memory/dynamic_arrays.py"):
            continue
        offenders += [f"{rel}:{line} calls {attr}"
                      for line, attr in _attribute_reads(path, ARRAY_EXIT_HELPERS)]
    assert not offenders, "\n".join(offenders)
