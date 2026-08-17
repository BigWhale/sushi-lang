"""Regression tests for #123 under borrow by default (2026-08-16)."""
from __future__ import annotations

import pytest

from tests.unit.test_ffi import _emit_ir, _function_body, _count_in_function

_DROP_CALL = 'call void %"closure_drop_fn'


def _counts(tmp_path, src):
    ir_text = _emit_ir(tmp_path, src)
    return (
        _count_in_function(ir_text, "run", 'call i8* @"malloc"'),
        _function_body(ir_text, "run").count(_DROP_CALL),
        _function_body(ir_text, "apply").count(_DROP_CALL),
    )


# Two callers with the same argument: one that exits through `??` (two exit blocks) and
# one that exits through `.realise()` (one). Both are parametrized over both modes.
CALLERS = {
    "qq": (
        "fn run() i32:\n"
        "    let i32 k = 7\n"
        "    return Result.Ok(apply({arg}, 10)??)\n"
    ),
    "realise": (
        "fn run() i32:\n"
        "    let i32 k = 7\n"
        "    let i32 v = apply({arg}, 10).realise(-1)\n"
        "    return Result.Ok(v)\n"
    ),
}

_MAIN = (
    "\nfn main() i32:\n"
    "    let i32 r = run().realise(-1)\n"
    "    return Result.Ok(0)\n"
)


def _program(caller: str, nom: bool) -> str:
    marker = "nom " if nom else ""
    return (
        f"fn apply({marker}fn(i32) -> i32 f, i32 x) i32:\n"
        "    return Result.Ok(f(x)??)\n"
        "\n"
        + CALLERS[caller].format(arg=f"{marker}|i32 x| x + k")
        + _MAIN
    )


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_a_borrow_parameter_leaves_the_env_with_the_caller(tmp_path, caller):
    """The flip: the caller mallocs the env, keeps it, and frees it on each exit path."""
    mallocs, caller_drops, callee_drops = _counts(tmp_path, _program(caller, nom=False))
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    assert caller_drops >= 1, (
        f"the caller owns an inline-closure argument at a borrow parameter and must free "
        f"it, got {caller_drops} drops; the env is leaking"
    )
    assert callee_drops == 0, (
        f"a borrow parameter must never be freed by the callee, got {callee_drops} drops; "
        "that is a double free of the env the caller still owns"
    )


@pytest.mark.parametrize("caller", sorted(CALLERS))
def test_a_nom_parameter_takes_the_env_to_the_callee(tmp_path, caller):
    """The twin: `nom` transfers, so the drop moves to the callee and the caller emits none."""
    mallocs, caller_drops, callee_drops = _counts(tmp_path, _program(caller, nom=True))
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    assert caller_drops == 0, (
        f"the caller must not drop a transferred inline-closure argument, got {caller_drops}; "
        "a caller-side drop double-frees the env the callee now owns"
    )
    # One guarded drop per mutually-exclusive callee exit block: the `??` propagate path
    # AND the success return. Exactly one runs at runtime, so this is no double free.
    assert callee_drops == 2, (
        f"expected the callee to free its `nom` fn parameter on both exit paths, got "
        f"{callee_drops}; the parameter is leaking"
    )
