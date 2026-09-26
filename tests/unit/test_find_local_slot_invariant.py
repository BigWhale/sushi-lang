"""`find_local_slot` must fail as a registered diagnostic, never as a bare `KeyError`."""
from __future__ import annotations

import ast
from pathlib import Path



BACKEND_ROOT = Path(__file__).parent.parent.parent / "sushi_lang" / "backend"








def _calls_find_local_slot(node: ast.AST) -> bool:
    """True if `node`'s subtree calls `find_local_slot` (not `try_find_local_slot`)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr == "find_local_slot":
                return True
    return False


def _handles_keyerror(handler: ast.ExceptHandler) -> bool:
    """True if `handler` catches KeyError (bare `except:` catches it too)."""
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id == "KeyError" for n in names)


def _keyerror_catchers() -> list[tuple[str, int]]:
    """Return (relpath, lineno) for each `try` that guards find_local_slot with KeyError."""
    hits = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not any(_calls_find_local_slot(stmt) for stmt in node.body):
                continue
            if any(_handles_keyerror(h) for h in node.handlers):
                hits.append((str(path.relative_to(BACKEND_ROOT.parent.parent)),
                             node.lineno))
    return hits


def test_no_backend_caller_catches_keyerror():
    """A caller that needs "is this a local?" uses try_find_local_slot, not except."""
    hits = _keyerror_catchers()
    assert not hits, (
        "these find_local_slot callers catch KeyError instead of using "
        f"try_find_local_slot: {hits}"
    )
