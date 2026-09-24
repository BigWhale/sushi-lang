"""Every owning binding goes through `register_owning_value` (#833).

`test_owning_value_registry_is_total.py` proves that the router gives each owner kind a
registry. It cannot see a site that copies the router's body instead of calling it, and
a copy goes out of step with the router: the `let` path tested the UNRESOLVED type where
the router tests the resolved one. So outside `memory/scopes.py`, where the router lives,
no backend module calls the container registrations directly.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

ROUTER_ONLY = {"register_own", "register_list"}


def _direct_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    return [(node.lineno, node.func.attr) for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ROUTER_ONLY]


def test_only_the_router_calls_the_container_registrations():
    offenders = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix()
        if rel == "memory/scopes.py":
            continue
        offenders += [f"{rel}:{line} calls {name}"
                      for line, name in _direct_calls(path.read_text())]
    assert not offenders, (
        "register an owning slot through memory.register_owning_value:\n"
        + "\n".join(offenders))


def test_the_router_still_makes_the_calls():
    calls = {name for _, name in _direct_calls((BACKEND / "memory" / "scopes.py").read_text())}
    assert calls == ROUTER_ONLY
