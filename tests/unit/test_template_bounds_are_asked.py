"""Every place that decides "does this template apply to this instance" asks ONE predicate.

An extension template and a perk template can put a bound in their target
(`extend List@(T: Clone)`, #1070). An instance whose type argument fails the bound gets
no copy, and a call of it is refused at the call (CE4006). The cutters, the overrides
(`template_covers`) and the call-site rung must give the same answer: if an override
says "covered" and no copy is cut, the backend calls a method that does not exist.

So each of them calls `target_bounds_hold` (`generics/constraints.py`), directly or
through `bounds_hold_for` or `_refuses_unmet_bounds`, and a function that cuts or queues
a copy must be one of them. A new cutter must join the list. The gate scans the Python
source; it starts no compiler.
"""
from __future__ import annotations

import ast
from pathlib import Path

SUSHI_LANG = Path(__file__).resolve().parents[2] / "sushi_lang"

PREDICATES = {"target_bounds_hold", "bounds_hold_for", "_refuses_unmet_bounds"}

# (module, function) -> what it decides. Each one must call a predicate.
ASKERS = {
    ("semantics/generics/extensions.py", "monomorphize_all_extension_methods"):
        "the eager and late generic-target extension copies",
    ("semantics/generics/extensions.py", "monomorphize_all_perk_impls"):
        "the eager and late perk-template copies",
    ("semantics/generics/array_perk_copies.py", "ArrayPerkCopies.__call__"):
        "the array perk-template copies, cut on a miss",
    ("semantics/passes/types/calls/statics.py", "_interned_static_target"):
        "the late copy of a static",
    ("semantics/passes/types/calls/methods.py", "instantiate_array_extension"):
        "the array extension copy, queued at a call",
    ("semantics/passes/types/calls/methods.py", "resolve_method_generic_extension"):
        "the method-generic copy, queued at a call",
    ("semantics/passes/types/calls/methods.py", "_answer_from_template"):
        "the call-site rung for every receiver",
    ("semantics/generics/contract_walk.py", "template_covers"):
        "the overrides and the constraint check",
    ("semantics/passes/collect/perks.py", "PerkImplementationTable._template_method"):
        "a perk template's method over an opaque instance",
}

# The functions that cut or queue a copy of a template.
CUTTING_CALLS = {"monomorphize_extension_method", "monomorphize_perk_impl",
                 "_queue_extension_instantiation", "_add_late_static_copy"}

# A function that cuts or queues a copy and asks no predicate itself, with the reason.
CUT_WITHOUT_ASKING = {
    ("semantics/semantic_analyzer.py", "SemanticAnalyzer._drain_pending_array_extensions"):
        "cuts what a call queued, and every queue writer asked",
    ("semantics/passes/types/calls/statics.py", "_add_late_static_copy"):
        "its one caller, _interned_static_target, asked",
    ("semantics/passes/types/templates.py", "check_extension_template"):
        "a check copy over opaque parameters, whose receiver bounds are its promises",
}


def _functions(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[f"{prefix}{child.name}"] = child
    visit(tree, "")
    return found


def _called(function: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_each_asker_calls_the_predicate():
    missing = []
    for (module, name), what in ASKERS.items():
        functions = _functions(SUSHI_LANG / module)
        assert name in functions, f"{module}: no function {name} ({what})"
        if not _called(functions[name]) & PREDICATES:
            missing.append(f"{module}:{name} ({what})")
    assert missing == [], "these do not ask target_bounds_hold:\n" + "\n".join(missing)


def test_every_cutter_is_an_asker():
    """A function that cuts or queues a copy is on the list, or says why it needs not ask."""
    allowed = set(ASKERS) | set(CUT_WITHOUT_ASKING)
    strays = []
    for path in SUSHI_LANG.rglob("*.py"):
        module = path.relative_to(SUSHI_LANG).as_posix()
        for name, function in _functions(path).items():
            if _called(function) & CUTTING_CALLS and (module, name) not in allowed:
                strays.append(f"{module}:{name}")
    assert strays == [], ("a new cutter must ask target_bounds_hold and join ASKERS:\n"
                          + "\n".join(strays))
