"""One ladder for a method call over SHIR, and a SHIR home for each fact of its answer (#753).

The question "what does `x.f(args)` name?" was answered twice on the AST, in two arm
orders, and each answer copied a different set of stamps back onto the call node. Every
stamp in that gap had a miscompile behind it: a `poke self` receiver passed by value and
its write lost (#326, #327), a `nom` parameter made inert, a call bound to the copy cut for
another type.

On SHIR the typecheck pass walks ONE ladder, `resolve_dotted` (docs/design/ir.md 7.3),
and writes its answer as rows of `TypeckResults`: no stamp. This file holds the shape:
one home for the SHIR ladder, a SHIR home for each fact that the AST stamp set carried,
and one maker of a `BuiltinMethod`, so that its family is always a row of
`METHOD_TYPE_REGISTRY`. The AST ladder went with the inference of the early passes
(ruling E4).
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from sushi_lang.semantics.ast import DotCall, MethodCall, Node
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.shir.callee import BuiltinMethod, Method
from sushi_lang.semantics.shir.typeck import ResolvedCall, TypeckResults

PACKAGE = Path(__file__).resolve().parents[2] / "sushi_lang"
TYPES_PASS = PACKAGE / "semantics" / "passes" / "types"
LADDER = TYPES_PASS / "calls" / "dotcall.py"

#: Each fact that the AST ladder copied back onto the call node, and the SHIR field that
#: holds it now: a field of the callee row, or a row kind of `TypeckResults`. The AST
#: ladder is deleted, and so are these fields of the AST call nodes.
SHIR_HOMES = {
    "callee_builtin_family": (BuiltinMethod, "family"),
    "callee_extension_unit": (ResolvedCall, "unit"),
    "callee_param_modes": (TypeckResults, "convention_of"),
    "resolved_enum_type": (TypeckResults, "type_of"),
    "callee_self_mode": (Method, "receiver"),
    "callee_borrows": (TypeckResults, "sources_of"),
}

#: The fields of an AST call node that are no written part of the call and that stay for
#: now: no pass of the body writes them, and Phase 6 deletes them with their AST readers.
#: `namespace_ref` is written by the constant evaluator for a constant initializer.
_STILL_ON_THE_AST = ("inferred_return_type", "resolved_enum_type", "namespace_ref")

#: Shared fields that are no fact of a resolved method: the written parts of the call, and
#: what the other call shapes and the AST borrow helpers write on their own nodes.
_NOT_A_METHOD_STAMP = ("receiver", "method", "args", "field_names")

#: Every `MethodCall(...)` construction under the typecheck pass, with why it is there.
_METHOD_CALL_VIEWS: dict[str, int] = {}


def _field_names(node_class) -> set[str]:
    return {f.name for f in dataclasses.fields(node_class)}


def _modules(root: Path = TYPES_PASS) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _tree(module: Path) -> ast.AST:
    return ast.parse(module.read_text(encoding="utf-8"))


def _count_calls(tree: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == name)


def _count_defs(tree: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _homes(name: str) -> tuple[dict[str, int], dict[str, int]]:
    """Where the typecheck pass defines `name`, and where it calls it."""
    defs: dict[str, int] = {}
    calls: dict[str, int] = {}
    for module in _modules():
        tree = _tree(module)
        rel = module.relative_to(TYPES_PASS).as_posix()
        if _count_defs(tree, name):
            defs[rel] = _count_defs(tree, name)
        if _count_calls(tree, name):
            calls[rel] = _count_calls(tree, name)
    return defs, calls


def test_no_ast_call_node_carries_a_fact_of_a_resolved_method():
    """The fields that the AST call nodes share are the written parts of the call, or a
    field that no body pass writes. No fact of `SHIR_HOMES` but one of those is there."""
    shared = (_field_names(MethodCall) & _field_names(DotCall)) - _field_names(Node)
    left = sorted(shared - set(_NOT_A_METHOD_STAMP) - set(_STILL_ON_THE_AST))
    assert not left, (
        "an AST call node carries a field that is no written part of the call: "
        + ", ".join(left) + "\nA fact of a resolved method is a row of TypeckResults.")
    assert set(_NOT_A_METHOD_STAMP) <= shared, f"the field reader found only {sorted(shared)}"


@pytest.mark.parametrize("stamp", sorted(SHIR_HOMES))
def test_the_shir_home_exists(stamp):
    """The SHIR home of a fact is a field of the callee row or a row kind of the table."""
    owner, name = SHIR_HOMES[stamp]
    names = (_field_names(owner) if dataclasses.is_dataclass(owner)
             and not issubclass(owner, TypeckResults) else set(dir(owner)))
    assert name in names, f"{owner.__name__} has no {name!r}, the home of {stamp!r}"


def test_the_stamp_copy_is_gone():
    """The SHIR ladder writes rows, so no module copies the stamp set any more."""
    for module in _modules():
        tree = _tree(module)
        assert not _count_defs(tree, "copy_callee_stamps"), module
        assert not any(isinstance(n, ast.Name) and n.id == "CALLEE_STAMPS"
                       for n in ast.walk(tree)), module


def test_the_shir_ladder_has_one_home():
    """`resolve_dotted` is written once, and the method call arm of the pass reads it."""
    defs, calls = _homes("resolve_dotted")
    assert defs == {"calls/dotcall.py": 1}, f"the SHIR ladder is defined in {defs}"
    assert calls == {"visit/calls.py": 1}, (
        f"the SHIR ladder is walked from {calls}: the method call arm, and nothing else")


def test_no_ast_ladder_is_left():
    """The early passes do not infer (ruling E4), so no AST ladder answers a call."""
    defs, calls = _homes("resolve_dotcall")
    assert defs == {} and calls == {}, (defs, calls)


def _makes_a_shir_builtin_method(tree: ast.AST) -> int:
    """The calls of the SHIR `BuiltinMethod` in a module that imports it from shir."""
    imported = any(isinstance(n, ast.ImportFrom)
                   and n.module == "sushi_lang.semantics.shir.callee"
                   and any(alias.name == "BuiltinMethod" for alias in n.names)
                   for n in ast.walk(tree))
    return _count_calls(tree, "BuiltinMethod") if imported else 0


def test_a_builtin_method_is_made_by_its_family():
    """Only `MethodFamily.callee` makes a `BuiltinMethod`, so its family is a row of the
    table: a free string cannot name a family that does not exist."""
    makers = {module.relative_to(PACKAGE).as_posix(): count
              for module in _modules(PACKAGE)
              if (count := _makes_a_shir_builtin_method(_tree(module)))}
    assert makers == {"semantics/passes/types/method_registry.py": 1}, (
        f"a BuiltinMethod is made in {makers}; make it with MethodFamily.callee")


def test_a_builtin_method_states_its_receiver_mode():
    """`nom_self` has no default: a maker of a `BuiltinMethod` cannot forget the mode."""
    field = {f.name: f for f in dataclasses.fields(BuiltinMethod)}["nom_self"]
    assert field.default is dataclasses.MISSING


@pytest.mark.parametrize("family", [f.name for f in METHOD_TYPE_REGISTRY.families])
def test_a_family_names_itself_in_its_callee(family):
    method = METHOD_TYPE_REGISTRY.family(family).callee("probe")
    assert method == BuiltinMethod(family, "probe", nom_self=False)


def test_no_second_view_of_a_call():
    """Every `MethodCall(...)` the pass builds is a named one."""
    found: dict[str, int] = {}
    for module in _modules():
        count = _count_calls(_tree(module), "MethodCall")
        if count:
            found[module.relative_to(TYPES_PASS).as_posix()] = count
    assert found == _METHOD_CALL_VIEWS, (
        f"the pass builds MethodCall views in {found}, the table says "
        f"{_METHOD_CALL_VIEWS}. A new one is a second answer to what a call NAMES.")


def test_the_ladder_reader_sees_the_module():
    """The always-fires control for the readers above."""
    assert LADDER.exists(), "calls/dotcall.py is gone"
    assert len(_modules()) > 10, "the module reader found almost nothing"
    assert _makes_a_shir_builtin_method(ast.parse(
        "from sushi_lang.semantics.shir.callee import BuiltinMethod\n"
        "BuiltinMethod('primitive', 'to_str')\n")) == 1
