"""A user-written method is checked in ONE place, perk implementation or extension (#752).

`validate_method_call` held four jobs: a receiver-kind dispatcher, a per-family arity
check, a perk arm that was the same twenty lines as the extension arm with the same two
error codes, and the extension-resolution ladder. The comment beside the perk arm said
the two arms were made identical ON PURPOSE, which is the argument for writing them once.

What stands now is a ladder: the built-in families, the perk implementation, the clone
gate, the rest of the families, the extension. A perk method and an extension method are
one rule -- a user-written method on a type -- so one rule gets one checker, and the
receiver mode a marked `self` carries is stamped in one place.

The counts below are RATCHETS: each may go down and none may go up. A second home for
any of them is the shape that let the two arms drift apart in the first place.
"""
from __future__ import annotations

import ast
from pathlib import Path

METHODS = (Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics"
           / "passes" / "types" / "calls" / "methods.py")

#: `validate_method_call` reads as a ladder of named steps and nothing else.
_LADDER_COMPLEXITY = 12

#: How many CALL SITES each step may have. A row may go down and none may go up.
_ONE_HOME = {
    # The declared parameter modes, stamped for the borrow pass and the backend.
    "_stamp_param_modes": 1,
    # The one checker a user-written method takes, whichever table answered it.
    "_check_user_method": 2,
    # The receiver mode a marked `self` carries.
    "_check_receiver_mode": 1,
}

#: The stamp itself is one assignment, wherever it stands.
_SELF_MODE_STAMP = "callee_self_mode"


def _source() -> str:
    return METHODS.read_text(encoding="utf-8")


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(_source(), filename=str(METHODS))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{METHODS.name} has no {name}")


def _complexity(node: ast.AST) -> int:
    """McCabe: one, plus every branch point ruff counts."""
    branches = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                              ast.With, ast.Assert, ast.IfExp)):
            branches += 1
        elif isinstance(child, ast.BoolOp):
            branches += len(child.values) - 1
    return branches + 1


def test_validate_method_call_is_a_ladder():
    """No loop and no arm: every step is a named call that answers whether it ran."""
    fn = _function("validate_method_call")
    loops = [node for node in ast.walk(fn) if isinstance(node, (ast.For, ast.While))]
    assert not loops, (
        f"validate_method_call carries {len(loops)} loop(s); an argument loop belongs "
        "in the one checker, not in the dispatcher")
    assert _complexity(fn) <= _LADDER_COMPLEXITY, (
        f"validate_method_call is at complexity {_complexity(fn)}, over the ratchet of "
        f"{_LADDER_COMPLEXITY}. A new step is a named function, not a new arm.")


def test_the_complexity_reader_sees_a_branch():
    """The always-fires control: a reader that answers 1 for everything proves nothing."""
    probe = ast.parse("def f(a, b):\n"
                      "    if a and b:\n"
                      "        for x in a:\n"
                      "            pass\n")
    assert _complexity(probe.body[0]) == 4


def _call_sites() -> dict[str, int]:
    """Every call in the module, counted by the name it calls."""
    found: dict[str, int] = {}
    for node in ast.walk(ast.parse(_source(), filename=str(METHODS))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found[node.func.id] = found.get(node.func.id, 0) + 1
    return found


def test_each_step_has_one_home():
    sites = _call_sites()
    offenders = [f"{name}: {sites.get(name, 0)} call sites, ratchet {ceiling}"
                 for name, ceiling in sorted(_ONE_HOME.items())
                 if sites.get(name, 0) > ceiling]
    assert not offenders, (
        "a step of the method-call check has a second home:\n  " + "\n  ".join(offenders)
        + "\nThe perk arm and the extension arm drifted apart exactly this way.")


def test_the_self_mode_is_stamped_once():
    stamps = [node for node in ast.walk(ast.parse(_source(), filename=str(METHODS)))
              if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Attribute)
                      and target.attr == _SELF_MODE_STAMP for target in node.targets)]
    assert len(stamps) == 1, (
        f"the receiver mode is stamped at {len(stamps)} places; the borrow pass and the "
        "backend both read that stamp, so it has one writer")


def test_the_call_site_reader_finds_every_step():
    """The always-fires control for the readers above."""
    sites = _call_sites()
    missing = [name for name in _ONE_HOME if name not in sites]
    assert not missing, ("the shape changed under this gate: " + ", ".join(missing))
    assert _SELF_MODE_STAMP in _source()


def test_the_one_checker_serves_both_tables():
    """The perk rung and the extension rung reach the same checker."""
    for rung in ("_validate_perk_method", "_validate_extension_call"):
        body = ast.unparse(_function(rung))
        assert "_check_user_method(" in body, (
            f"{rung} checks its arguments itself instead of reading the one checker")
