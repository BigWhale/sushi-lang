"""`find_local_slot` must fail as a registered diagnostic, never as a bare `KeyError`.

A name the semantic passes already accepted must resolve in the backend. When it does
not, that is an internal-invariant violation and it belongs in the diagnostic channel
(Tier 4.7) like every other compiler failure. It used to raise a bare
`KeyError("undefined name: X")`, which the top-level guard rendered as an anonymous
**CE0000** -- so #248's five missed address sites all reported as "internal compiler
error: KeyError" with nothing pointing at the actual gap.

Two mechanisms, and this file pins both:

  find_local_slot      -- the assertive form. Raises CE0055 ("unknown variable or
                          constant"), the code `emit_name`'s own dead end already used.
  try_find_local_slot  -- the interrogative form, for callers that legitimately ask
                          "is this a local?" and have a real answer for "no" (a global
                          constant, a top-level function reference, a struct field).

The source gate is the part that keeps this honest: catching `KeyError` around a
`find_local_slot` call is how a caller opts out of the diagnostic channel, and the four
that did so are exactly the callers that should be using `try_find_local_slot`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sushi_lang.backend.codegen_llvm import LLVMCodegen
from sushi_lang.internals.diagnostics import InternalCompilerError

BACKEND_ROOT = Path(__file__).parent.parent.parent / "sushi_lang" / "backend"


def test_find_local_slot_raises_registered_diagnostic():
    """An unknown name is CE0055, not a bare KeyError."""
    memory = LLVMCodegen().memory
    with pytest.raises(InternalCompilerError) as excinfo:
        memory.find_local_slot("no_such_name")
    assert excinfo.value.code == "CE0055"


def test_find_local_slot_does_not_raise_keyerror():
    """Explicitly: a KeyError must not escape (it renders as an anonymous CE0000)."""
    memory = LLVMCodegen().memory
    try:
        memory.find_local_slot("no_such_name")
    except InternalCompilerError:
        pass
    except KeyError as exc:  # pragma: no cover - the regression this file exists for
        pytest.fail(f"find_local_slot raised a bare KeyError: {exc!r}")


def test_try_find_local_slot_returns_none():
    """The interrogative form answers "not a local" without raising."""
    memory = LLVMCodegen().memory
    assert memory.try_find_local_slot("no_such_name") is None


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
    """Return (relpath, lineno) for each `try` that guards find_local_slot with KeyError.

    Read from the AST rather than by line proximity: `borrow.py` and `structs.py` put
    their `except KeyError` a dozen lines below the call, so a text window silently
    missed exactly the callers this gate exists to catch.
    """
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
