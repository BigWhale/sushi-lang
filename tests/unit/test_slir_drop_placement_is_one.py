"""Drop placement is ONE module of SHIR-to-SLIR lowering (docs/design/ir.md 8.10, 10.3).

The port of `test_scope_exit_walk_is_one.py` to SLIR: the drops of a scope exit, of a
moved local and of the old value of a rebind are placed by `semantics/slir/drops.py`, the
newest declaration first. So no other module of the compiler builds a SLIR `Drop` or a
`DropIfSet`. The old gate stays until Gate 4, with the scope-exit walk of the backend.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
SLIR = PACKAGE / "semantics" / "slir"
DROP_CLASSES = {"Drop", "DropIfSet"}
#: The module that places the drops, and the module that declares their classes.
ALLOWED = {SLIR / "drops.py", SLIR / "statements.py"}


def _builds(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in DROP_CLASSES:
            lines.append(node.lineno)
    return lines


def test_only_the_drop_placement_builds_a_drop():
    offenders = [f"{path.relative_to(PACKAGE).as_posix()}:{line}"
                 for path in sorted(PACKAGE.rglob("*.py")) if path not in ALLOWED
                 for line in _builds(path)]
    assert not offenders, (
        "a SLIR drop is placed by semantics/slir/drops.py alone (ir.md 8.10):\n"
        + "\n".join(offenders))


def test_the_drop_placement_builds_the_drops():
    assert _builds(SLIR / "drops.py"), "the scan finds no drop in the drop placement"
