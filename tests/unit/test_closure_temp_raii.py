"""Regression tests for #123 under the by-value-parameter ruling (2026-08-14).

A capturing closure created directly as a call argument heap-allocates an
environment (`{...}` captured fields) via `malloc` in the CALLER. The callee owns
its by-value `fn(...)` parameter (the ruling: a by-value parameter is a consuming
position), so the guarded type-erased drop (`call void %"closure_drop_fn"`) is
emitted in the CALLEE, one per mutually-exclusive exit path. The caller emits no
drop for a transferred argument -- a caller-side drop would be the #123-era
convention and would now double-free.

The leak is silent at runtime, so exit-code tests cannot catch it. These assert
where the malloc and the drop live, so both an under-free (leak) and a caller-side
re-registration (double free) are caught.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _function_body, _count_in_function

_DROP_CALL = 'call void %"closure_drop_fn'


def test_inline_closure_arg_env_freed_on_both_qq_paths(tmp_path):
    """An inline capturing closure argument: the caller mallocs the env and emits NO
    drop; the callee owns the parameter and drops it on BOTH its exit paths (the `??`
    error-propagate path and the success return). Exactly one runs at runtime.
    """
    src = (
        "fn apply(fn(i32) -> i32 f, i32 x) i32:\n"
        "    return Result.Ok(f(x)??)\n"
        "\n"
        "fn run() i32:\n"
        "    let i32 k = 7\n"
        "    return Result.Ok(apply(|i32 x| x + k, 10)??)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = run().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)

    mallocs = _count_in_function(ir_text, "run", 'call i8* @"malloc"')
    caller_drops = _function_body(ir_text, "run").count(_DROP_CALL)
    callee_drops = _function_body(ir_text, "apply").count(_DROP_CALL)
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    assert caller_drops == 0, (
        f"the caller must not drop a transferred inline-closure argument, got {caller_drops}; "
        "a caller-side drop double-frees the env the callee now owns"
    )
    # One guarded drop per mutually-exclusive callee exit block: the `??` propagate
    # path AND the success return. Exactly one runs at runtime, so this is no double free.
    assert callee_drops == 2, (
        f"expected the callee to free its fn parameter on both exit paths, got {callee_drops}; "
        "the by-value fn parameter is leaking"
    )


def test_inline_closure_arg_env_freed_single_return(tmp_path):
    """Same transfer, single-return caller: one malloc in the caller, no caller drop,
    and the callee frees its parameter on each of its exit paths."""
    src = (
        "fn apply(fn(i32) -> i32 f, i32 x) i32:\n"
        "    return Result.Ok(f(x)??)\n"
        "\n"
        "fn run() i32:\n"
        "    let i32 k = 7\n"
        "    let i32 v = apply(|i32 x| x + k, 10).realise(-1)\n"
        "    return Result.Ok(v)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = run().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)

    mallocs = _count_in_function(ir_text, "run", 'call i8* @"malloc"')
    caller_drops = _function_body(ir_text, "run").count(_DROP_CALL)
    callee_drops = _function_body(ir_text, "apply").count(_DROP_CALL)
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    assert caller_drops == 0, (
        f"single-return: the caller must not drop a transferred argument, got {caller_drops}"
    )
    assert callee_drops == 2, (
        f"expected the callee to free its fn parameter on both exit paths, got {callee_drops}"
    )
