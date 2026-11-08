"""A user-written method is checked in one place, perk implementation or extension (#752).

A perk method and an extension method are one rule: a user-written method on a type. Its
arguments go through one checker, `check_method_arguments` (`visit/calls.py`), and the
perk arm and the extension arm both call it. The gate reads the source of the typecheck pass.
"""
from __future__ import annotations

import ast
from pathlib import Path

TYPES_PASS = (Path(__file__).resolve().parents[2]
              / "sushi_lang" / "semantics" / "passes" / "types")
CHECKER = "check_method_arguments"
#: (module, function) of each arm that must reach the checker.
ARMS = {("visit/perk_calls.py", "check_perk_call"), ("visit/calls.py", "_check_user_method")}


def _sites() -> tuple[set[str], set[tuple[str, str]]]:
    """The modules that define the checker, and the (module, function) that call it."""
    defs, calls = set(), set()
    for path in sorted(TYPES_PASS.rglob("*.py")):
        module = path.relative_to(TYPES_PASS).as_posix()
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, ast.FunctionDef):
                continue
            if fn.name == CHECKER:
                defs.add(module)
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == CHECKER for n in ast.walk(fn)):
                calls.add((module, fn.name))
    return defs, calls


def test_the_perk_arm_and_the_extension_arm_read_one_checker():
    defs, calls = _sites()
    assert defs == {"visit/calls.py"}, f"the method-argument checker is defined in {defs}"
    assert ARMS <= calls, f"an arm checks its arguments itself: {sorted(ARMS - calls)}"
