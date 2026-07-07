"""Regression tests for #123: inline capturing-closure call argument leaks its env.

A capturing closure created directly as a call argument heap-allocates an
environment (`{...}` captured fields) via `malloc`. Bound to a `let` local it is
registered for RAII cleanup and freed on every exit path; passed *inline* as an
argument it has no owner, so its env used to leak (~16 bytes/closure).

The leak is silent at runtime, so exit-code tests cannot catch it. These assert the
env is freed by counting the guarded type-erased drop invocation
(`call void %"closure_drop_fn"`) inside the enclosing function -- one per
mutually-exclusive exit path, matching the malloc, so both the leak (under-free) and
any double-free are caught.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _function_body, _count_in_function

_DROP_CALL = 'call void %"closure_drop_fn'


def test_inline_closure_arg_env_freed_on_both_qq_paths(tmp_path):
    """An inline capturing closure argument before a `??`: its env is freed on BOTH
    the `??` error-propagate path and the success/return path (no leak, no double-free).
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
    drops = _function_body(ir_text, "run").count(_DROP_CALL)
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    # One guarded drop per mutually-exclusive exit block: the `??` propagate path AND
    # the success return. Exactly one runs at runtime, so this is no double free.
    assert drops == 2, (
        f"expected the inline closure env to be freed on both exit paths, got {drops}; "
        "the inline-argument closure env is leaking"
    )


def test_inline_closure_arg_env_freed_single_return(tmp_path):
    """An inline capturing closure argument in a single-return function: its env is
    freed exactly once (no double-free)."""
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
    drops = _function_body(ir_text, "run").count(_DROP_CALL)
    assert mallocs == 1, f"expected exactly one closure-env malloc, got {mallocs}"
    assert drops == 1, f"single-return: closure env must be freed exactly once, got {drops}"
