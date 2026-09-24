"""The two passes that run before the typecheck pass infer types and write no stamp (#806).

The `instantiate` pass and the `monomorphize` pass type a generic call's arguments
through the typecheck pass's own inferrer. Inference writes stamps on the AST, and the
backend reads them, so an early pass that stamps a node from tables that are not
complete yet leaves a value that only a later overwrite corrects.

The walk stops at the entry of the `resolve` stage, which is the first stage after the
two early passes. A second run goes through the typecheck pass over the same source and
is the control: it must find every stamp name in use, or the first assertion proves
nothing.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.ast_walk import walk_nodes
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer

STAMPS = (
    "inferred_return_type",
    "inferred_element_type",
    "resolved_enum_type",
    "resolved_struct_type",
    "callee_method_type_args",
)

SOURCE = """\
struct Box@(T):
    T v

enum Sign:
    Plus(i32)
    Minus

extend Box@(T) pick@(U)(U other) U:
    return other

fn tag@(T)(T x) i32:
    return Result.Ok(1)

fn wrap@(T)(nom T x) Box@(T):
    let i32 t = tag(x)??
    return Result.Ok(Box(x + t))

fn seven() i32:
    return Result.Ok(7)

fn main() i32:
    let i32[] arr = from([1, 2, 3])
    let i32 a = tag(arr[0]).realise(0)
    let i32 b = tag(seven().realise(0)).realise(0)
    let Box@(i32) c = wrap(nom a).realise(Box(0))
    let i32 d = tag(arr.get(1).realise(0)).realise(0)
    let i32 e = tag(Sign.Plus(5)).realise(0)
    let i32 f = tag(c.pick(4)).realise(0)
    let Own@(i32) o = Own.alloc(9)
    let i32 g = tag(o.get()).realise(0)
    println("{a} {b} {c.v} {d} {e} {f} {g}")
    return Result.Ok(0)
"""


class _StopBeforeResolve(ValueError):
    """Raised at the entry of the `resolve` stage; the analyze fixture catches it."""


def _stamps_on(analyzer) -> dict:
    """Every stamp name that a node reachable from a unit's AST carries, with a count."""
    found: dict = {}

    def visit(node) -> bool:
        for name in STAMPS:
            if getattr(node, name, None) is not None:
                found[name] = found.get(name, 0) + 1
        return True

    for unit in analyzer.unit_manager.units.values():
        if unit.ast is not None:
            walk_nodes(unit.ast, visit)
    return found


def test_early_passes_leave_no_inference_stamp(analyze_program, monkeypatch):
    def stop(self):
        raise _StopBeforeResolve()

    monkeypatch.setattr(SemanticAnalyzer, "_resolve_types", stop)
    analysis = analyze_program(SOURCE)
    assert analysis.analyzer is not None
    assert not analysis.reporter.has_errors
    assert _stamps_on(analysis.analyzer) == {}


def test_control_the_typecheck_pass_writes_the_stamps(analyze_program):
    analysis = analyze_program(SOURCE)
    assert not analysis.reporter.has_errors
    found = _stamps_on(analysis.analyzer)
    missing = [name for name in STAMPS if name not in found]
    assert missing == [], found


@pytest.mark.parametrize("name", STAMPS)
def test_every_stamp_name_is_an_ast_field(name):
    from dataclasses import fields

    from sushi_lang.semantics import ast as A
    declared = {f.name for cls in vars(A).values()
                if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__")
                for f in fields(cls)}
    assert name in declared
