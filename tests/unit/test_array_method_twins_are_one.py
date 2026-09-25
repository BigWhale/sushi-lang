"""One operation on an array has one emitter, for both array kinds (#876).

A fixed array and a dynamic array differ in where the element count comes from and in how
the element pointer is found. The dispatcher knows both, so it gives `(data_ptr, count)` to
ONE emitter for `fill`, `reverse` and `get`. Two copies of one emitter drift.
"""
from __future__ import annotations

import ast
from pathlib import Path

ARRAYS = (Path(__file__).resolve().parents[2]
          / "sushi_lang" / "backend" / "types" / "arrays")
CORE = ARRAYS / "methods" / "core.py"
SAFE_ACCESS = ARRAYS / "methods" / "safe_access.py"
DISPATCHER = ARRAYS / "dispatcher.py"

RETIRED_TWINS = {
    CORE: {"emit_fixed_array_fill", "emit_dynamic_array_fill",
           "emit_fixed_array_reverse", "emit_dynamic_array_reverse"},
    SAFE_ACCESS: {"emit_fixed_array_get_maybe", "emit_dynamic_array_get_maybe"},
}

ONE_EMITTER = {
    "fill": "emit_array_fill",
    "reverse": "emit_array_reverse",
    "get": "emit_array_get_maybe",
    "first": "emit_array_get_maybe",
    "last": "emit_array_get_maybe",
}

HALVES = ("emit_fixed_array_method", "emit_dynamic_array_method")


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Name):
                names.add(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                names.add(call.func.attr)
    return names


def test_the_twins_are_gone():
    for path, twins in RETIRED_TWINS.items():
        left = twins & set(_functions(path))
        assert not left, f"{path.name}: {sorted(left)}"


def test_the_one_emitters_exist():
    declared = set(_functions(CORE)) | set(_functions(SAFE_ACCESS))
    assert set(ONE_EMITTER.values()) <= declared


def test_fill_is_the_counted_walk():
    assert "emit_container_walk" in _called_names(_functions(CORE)["emit_array_fill"])


def test_get_takes_no_to_i1():
    fn = _functions(SAFE_ACCESS)["emit_array_get_maybe"]
    assert "to_i1" not in {a.arg for a in fn.args.args}


def test_each_half_routes_through_the_one_emitter():
    functions = _functions(DISPATCHER)
    for half in HALVES:
        assert half in functions, f"the dispatcher declares no '{half}'"
        cases = {}
        for match in (n for n in ast.walk(functions[half]) if isinstance(n, ast.Match)):
            for case in match.cases:
                for value in (n.value for n in ast.walk(case.pattern)
                              if isinstance(n, ast.Constant) and isinstance(n.value, str)):
                    cases[value] = case
        for method, emitter in ONE_EMITTER.items():
            assert method in cases, f"{half}: no arm for '{method}'"
            called = set().union(*(_called_names(s) for s in cases[method].body))
            assert emitter in called, f"{half}: '{method}' does not call {emitter}"
        get_called = set().union(*(_called_names(s) for s in cases["get"].body))
        assert "_index_arg" in get_called, f"{half}: 'get' reads its index by hand"


def test_the_dispatcher_ends_in_an_internal_error():
    source = DISPATCHER.read_text(encoding="utf-8")
    assert "NotImplementedError" not in source
    for half in HALVES:
        assert "raise_internal_error" in _called_names(_functions(DISPATCHER)[half])
