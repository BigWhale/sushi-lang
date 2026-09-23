"""One solver for the leading type arguments of a generic call (#795).

"Unify the parameters, read the leading type arguments off the map, resolve each" was
written five times, and the copies did not agree: the monomorphize copy had no pack
handling and no lambda path, so a variadic or lambda call inside a generic body was
solved by one pass and not instantiated by the next. The gate reads the source: the
five call sites reach `solve_leading_type_args` (directly or through
`infer_flat_type_args`), and no module on the call path unifies for itself.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics"

SOLVER_NAMES = {"solve_leading_type_args", "infer_flat_type_args"}
UNIFY_NAMES = {"unify_types", "_unify_types_for_inference"}

MODULES = (
    "generics/instantiate/expressions.py",
    "generics/monomorphize/functions.py",
    "passes/types/calls/generics.py",
)

SITES = (
    ("generics/instantiate/expressions.py", "_infer_type_args_from_call"),
    ("generics/instantiate/expressions.py", "scan_generic_fn_reference"),
    ("generics/monomorphize/functions.py", "_call_type_args"),
    ("passes/types/calls/generics.py", "resolve_generic_fn_reference"),
    ("passes/types/calls/generics.py", "_infer_type_args_from_call_site"),
)


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _hand_rolled_unifications(source: str) -> list[int]:
    tree = ast.parse(source)
    return [sub.lineno for sub in ast.walk(tree)
            if isinstance(sub, ast.Call)
            and ((isinstance(sub.func, ast.Name) and sub.func.id in UNIFY_NAMES)
                 or (isinstance(sub.func, ast.Attribute) and sub.func.attr in UNIFY_NAMES))]


def _function(module: str, name: str) -> ast.AST | None:
    tree = ast.parse((ROOT / module).read_text())
    for sub in ast.walk(tree):
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == name:
            return sub
    return None


def test_control_the_scanner_sees_a_hand_rolled_unification():
    source = (
        "def f(self, p, a, m):\n"
        "    unify_types(p, a, m)\n"
        "    self.type_inferrer.unify_types(p, a, m)\n"
        "    _unify_types_for_inference(p, a, m)\n"
    )
    assert _hand_rolled_unifications(source) == [2, 3, 4]


def test_no_module_on_the_call_path_unifies_for_itself():
    offenders = {m: _hand_rolled_unifications((ROOT / m).read_text()) for m in MODULES}
    assert {m: lines for m, lines in offenders.items() if lines} == {}


def test_every_site_reaches_the_one_solver():
    missing = []
    for module, name in SITES:
        fn = _function(module, name)
        assert fn is not None, f"{module}:{name} is gone; update the gate"
        if not (_called_names(fn) & SOLVER_NAMES):
            missing.append(f"{module}:{name}")
    assert missing == []


def test_the_solver_is_the_one_home_of_leading_unification():
    source = (ROOT / "generics/pack_inference.py").read_text()
    assert "def solve_leading_type_args" in source
    assert _hand_rolled_unifications(source)
