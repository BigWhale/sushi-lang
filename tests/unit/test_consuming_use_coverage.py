"""Nothing may bypass the ownership seam. No allowlist.

Two seams, one rule. The old backend reaches each transfer through `backend/ownership.py`,
until Gate 4 deletes it with `MoveTracker`. The borrow pass on SHIR decides the `Ownership`
of each consuming use ONE time, through `classify()`, and writes the answer as a row of
`OwnershipResults` (docs/design/ir.md 7.9, 10.3): one function of the pass writes the row,
and the pass checks the table for each consuming use when it ends a body with no error
(`check_ownership`, CE0157). SHIR-to-SLIR lowering reads the row and makes `Move` or `Copy`.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "sushi_lang"
BACKEND = SOURCE / "backend"
BORROW = SOURCE / "semantics" / "passes" / "borrow"

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


# -- the borrow pass on SHIR (docs/design/ir.md 7.9) ------------------------------------


def _called(node: ast.Call) -> str | None:
    func = node.func
    return func.id if isinstance(func, ast.Name) else (
        func.attr if isinstance(func, ast.Attribute) else None)


def _functions_that_call(name: str) -> list[str]:
    """Each function under `passes/borrow/` that calls `name`, as `module:function`."""
    found: list[str] = []
    for path in sorted(BORROW.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            if any(isinstance(node, ast.Call) and _called(node) == name
                   for node in ast.walk(fn)):
                found.append(f"{path.name}:{fn.name}")
    return found


def test_one_function_of_the_borrow_pass_writes_the_ownership_of_a_consuming_use():
    """`classify()` answers one time per consuming use, and one function writes the answer:
    a second writer is a second derivation of the rule."""
    writers = _functions_that_call("record_ownership")
    assert len(writers) == 1, (
        f"the Ownership row of a consuming use has {len(writers)} writers in "
        f"passes/borrow/: {writers}. Write it in the one decision of the pass.")


def test_the_one_writer_asks_classify():
    """The one writer of the row writes what `classify()` answers, and nothing else."""
    (writer,) = _functions_that_call("record_ownership")
    module, function = writer.split(":")
    tree = ast.parse((BORROW / module).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == function)
    called = {_called(node) for node in ast.walk(fn) if isinstance(node, ast.Call)}
    assert "classify" in called, f"{writer} writes an Ownership that classify() did not answer"


def test_the_borrow_pass_checks_its_table_when_it_ends_a_body():
    """CE0157 finds a consuming use with no row: the check runs on each finished body."""
    assert _functions_that_call("check_ownership"), (
        "no function of passes/borrow/ calls check_ownership: a consuming use with no "
        "Ownership row would reach SLIR lowering")
