"""Nothing may bypass the ownership seam. No allowlist.

`backend/ownership.py` is the ONLY module allowed to state that a binding's ownership
moved. Every consuming position calls `consume()` / `bind()` / `relinquish()` /
`relinquish_temp()`, and those call the move-mark primitives. A backend module that
calls a primitive directly is a twelfth derivation of the ownership rule -- the exact
defect class this branch exists to remove (four point-fixes preceded the seam, and
each was a shipped bug).

The gate is static, like tests/unit/test_borrow_dispatch_is_total.py: the source IS
the contract. Two rings are checked:

  1. The NAMED move marks (`mark_struct_as_moved`, `mark_as_moved`, and the dead
     `mark_list_moved` / `mark_own_moved`, banned so they cannot come back as bypass
     channels) may be REFERENCED only from backend/ownership.py. Their definitions
     (plain `def` statements in memory/scopes.py and memory/dynamic_arrays.py) are not
     attribute references and do not trip the scan.
  2. The underlying tracker write, `<...>.moves.mark(...)`, may appear only inside the
     two modules that implement the named primitives (memory/scopes.py,
     memory/dynamic_arrays.py). `moves.unmark` is NOT banned: it states a
     re-initialization (a rebind sink's own bookkeeping), not a transfer.

Verified to fail by putting one direct call back (see the Phase 10 record in the
working log): the gate reported it and the suite went red.
"""
from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "sushi_lang" / "backend"

# Ring 1: the named primitives. Only the seam may reference these.
_MOVE_MARKS = {"mark_struct_as_moved", "mark_as_moved", "mark_list_moved", "mark_own_moved"}
_SEAM = BACKEND / "ownership.py"

# Ring 2: the tracker write. Only the primitive implementations may call it.
_TRACKER_IMPLS = {
    BACKEND / "memory" / "scopes.py",
    BACKEND / "memory" / "dynamic_arrays.py",
    BACKEND / "memory" / "moves.py",
}


def _backend_sources():
    for path in sorted(BACKEND.rglob("*.py")):
        yield path, ast.parse(path.read_text(), filename=str(path))


def _is_moves_mark(node: ast.Attribute) -> bool:
    """True for an attribute chain ending `.moves.mark`."""
    if node.attr != "mark":
        return False
    value = node.value
    return (isinstance(value, ast.Attribute) and value.attr == "moves") or (
        isinstance(value, ast.Name) and value.id == "moves"
    )


def test_move_marks_are_reached_only_from_the_seam():
    offenders: list[str] = []
    for path, tree in _backend_sources():
        if path == _SEAM:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _MOVE_MARKS:
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno} .{node.attr}")
    assert not offenders, (
        "a transfer primitive is referenced outside backend/ownership.py:\n  "
        + "\n  ".join(offenders)
        + "\nRoute the position through consume()/bind()/relinquish()/relinquish_temp() "
        "instead -- a direct move mark is a second derivation of the ownership rule."
    )


def test_tracker_write_stays_inside_the_primitive_implementations():
    offenders: list[str] = []
    for path, tree in _backend_sources():
        if path in _TRACKER_IMPLS:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _is_moves_mark(node):
                offenders.append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    assert not offenders, (
        "codegen.moves.mark() is called outside the primitive implementations:\n  "
        + "\n  ".join(offenders)
        + "\nThe tracker write belongs to mark_struct_as_moved / mark_as_moved, which "
        "only backend/ownership.py may call."
    )


def test_the_seam_itself_still_calls_the_primitives():
    """The mirror: if the primitives are renamed, the gate must not go quietly green."""
    tree = ast.parse(_SEAM.read_text(), filename=str(_SEAM))
    called = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _MOVE_MARKS
    }
    assert {"mark_struct_as_moved", "mark_as_moved"} <= called, (
        f"backend/ownership.py no longer calls the move-mark primitives (found {sorted(called)}); "
        "either the primitives were renamed (update this gate) or the seam stopped marking."
    )
