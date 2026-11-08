"""The early passes do not infer (PHASE3.md D9, ruling E4).

`instantiate` and `monomorphize` collect an instance from what a program WRITES: an
explicit type argument, a written type, a generic function value against the type of its
position. They type no expression. An inferred generic call is cut late, when the
typecheck pass asks for its instance. The gate reads the source of the two passes: no
module of them calls an inference entry.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang" / "semantics" / "generics"

PASSES = ("instantiate", "monomorphize")

INFERENCE_ENTRIES = {"infer_expression_type", "infer_call_arg_type", "infer_lambda_type",
                     "ReadOnlyInferrer"}


def _inference_calls(source: str) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None)
            if name in INFERENCE_ENTRIES:
                found.append((node.lineno, name))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in INFERENCE_ENTRIES:
                    found.append((node.lineno, alias.name))
    return found


def test_control_the_scanner_sees_an_inference_call():
    source = (
        "from x import infer_call_arg_type\n"
        "def f(self, v, e):\n"
        "    v.infer_expression_type(e)\n"
        "    infer_lambda_type(v, e, stamp=False)\n"
    )
    assert _inference_calls(source) == [
        (1, "infer_call_arg_type"), (3, "infer_expression_type"), (4, "infer_lambda_type")]


def test_no_early_pass_module_infers():
    offenders = {}
    for name in PASSES:
        modules = sorted((ROOT / name).glob("*.py"))
        assert modules, f"generics/{name}/ holds no module; update the gate"
        for path in modules:
            calls = _inference_calls(path.read_text())
            if calls:
                offenders[path.relative_to(ROOT).as_posix()] = calls
    assert offenders == {}
