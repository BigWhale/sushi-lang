"""One ladder for `X.Y(args)`, and one stamp set at the end of it (#753).

The question "what does a DotCall name?" was answered twice -- once by the validation half
and once by the inference half -- in two different arm orders, each on its own throwaway
`MethodCall`, and the two copied a different number of stamps back. Every stamp in that gap
has a miscompile behind it: a `poke self` receiver passed by value and its write lost
(#326, #327), a `nom` parameter made inert, a call bound to the copy cut for another type.

This file holds the shape: one `resolve_dotcall`, one `copy_callee_stamps`, and a stamp set
that cannot fall behind the node it copies from.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from sushi_lang.semantics.ast import DotCall, MethodCall, Node
from sushi_lang.semantics.passes.types.calls.dotcall import CALLEE_STAMPS

TYPES_PASS = (Path(__file__).resolve().parents[2]
              / "sushi_lang" / "semantics" / "passes" / "types")
LADDER = TYPES_PASS / "calls" / "dotcall.py"

#: What the DotCall hands INTO the temporary node instead of reading back off it. The
#: propagation stamp is what makes the `HashMap.new()` key gate reachable (#272) and the
#: namespace stamp says the receiver arrived qualified (#506).
_CARRIED_IN = ("resolved_struct_type", "namespace_ref")

#: Shared fields the method rung never writes. `callee_fn_type` belongs to the fn-field
#: rung, which stamps the DotCall itself; the rest are written by the other call shapes
#: (a plain call, an FFI extern) on their own nodes. A field added to BOTH node kinds and
#: named in none of the three tables is a stamp the ladder would drop in silence.
_NOT_A_METHOD_STAMP = ("receiver", "method", "args", "field_names", "callee_fn_type",
                       "callee_unresolved", "external_ref", "variadic_arg_types")

#: Every `MethodCall(...)` construction under the typecheck pass, with why it is there.
#: A third one is a second view of a call, which is what this file exists to refuse.
_METHOD_CALL_VIEWS = {
    "calls/dotcall.py": 1,      # the one temporary node the method rung resolves on
    "statements.py": 1,         # the `foreach` protocol's own `next()` call, not a DotCall
}


def _field_names(node_class) -> set[str]:
    return {f.name for f in dataclasses.fields(node_class)}


def _modules() -> list[Path]:
    return sorted(TYPES_PASS.rglob("*.py"))


def _count_calls(tree: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == name)


def _count_defs(tree: ast.AST, name: str) -> int:
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_the_stamp_set_covers_every_shared_field():
    """A field both node kinds declare is copied back, carried in, or named as neither.

    What every node carries -- the span, the ownership marks a value position writes -- is
    the base class's and belongs to the DotCall as a VALUE, never to its callee.
    """
    shared = (_field_names(MethodCall) & _field_names(DotCall)) - _field_names(Node)
    accounted = set(CALLEE_STAMPS) | set(_CARRIED_IN) | set(_NOT_A_METHOD_STAMP)
    missing = sorted(shared - accounted)
    assert not missing, (
        "a field the temporary node and the DotCall both carry is in no table: "
        + ", ".join(missing)
        + "\nAdd it to CALLEE_STAMPS in calls/dotcall.py if the resolved callee writes it, "
          "or to _CARRIED_IN / _NOT_A_METHOD_STAMP here with the reason it is neither."
    )


def test_the_shared_field_reader_sees_the_stamps():
    """The always-fires control: a vacuous reader answers the same empty set."""
    shared = (_field_names(MethodCall) & _field_names(DotCall)) - _field_names(Node)
    assert len(shared) > 10, f"the field reader found only {sorted(shared)}"
    assert set(CALLEE_STAMPS) <= shared, (
        "CALLEE_STAMPS names a field one of the two node kinds does not declare: "
        + ", ".join(sorted(set(CALLEE_STAMPS) - shared)))


def test_the_ladder_has_one_home():
    """`resolve_dotcall` is written once and read by exactly the two halves."""
    defs = {}
    calls = {}
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        rel = module.relative_to(TYPES_PASS).as_posix()
        if _count_defs(tree, "resolve_dotcall"):
            defs[rel] = _count_defs(tree, "resolve_dotcall")
        if _count_calls(tree, "resolve_dotcall"):
            calls[rel] = _count_calls(tree, "resolve_dotcall")

    assert defs == {"calls/dotcall.py": 1}, f"the ladder is defined in {defs}"
    assert calls == {"visitor.py": 2} or sum(calls.values()) == 2, (
        f"the ladder is walked {sum(calls.values())} times, from {calls} -- "
        "the validation half and the inference half, and nothing else")


def test_the_stamp_copy_has_one_home():
    """`copy_callee_stamps` is written once, and BOTH halves read it."""
    defs = 0
    calls = 0
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        defs += _count_defs(tree, "copy_callee_stamps")
        calls += _count_calls(tree, "copy_callee_stamps")
    assert defs == 1, f"copy_callee_stamps is defined {defs} times"
    assert calls == 2, (
        f"copy_callee_stamps is called {calls} times: the validation half and the "
        "inference half copy the same set, or one of them is back to its own list")


def test_no_second_view_of_a_call():
    """Every `MethodCall(...)` the pass builds is a named one."""
    found: dict[str, int] = {}
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        count = sum(1 for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "MethodCall")
        if count:
            found[module.relative_to(TYPES_PASS).as_posix()] = count
    assert found == _METHOD_CALL_VIEWS, (
        f"the pass builds MethodCall views in {found}, the table says "
        f"{_METHOD_CALL_VIEWS}. A new one is a second answer to what a call NAMES; "
        "route it through calls/dotcall.py instead.")


def test_the_ladder_reader_sees_the_module():
    """The always-fires control for the three readers above."""
    assert LADDER.exists(), "calls/dotcall.py is gone"
    assert len(_modules()) > 10, "the module reader found almost nothing"
