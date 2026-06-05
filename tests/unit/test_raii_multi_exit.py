"""Regression tests for #59: RAII cleanup on every exit path.

A heap-owning local (a dynamic array, or a struct with a dynamic-array field) must
be freed on *every* mutually-exclusive exit path, not just the first one emitted.
The historical bug marked the resource cleaned via a function-global flag during the
first early-exit's cleanup, so every later return / `??`-success path skipped the
free and leaked.

These assert the destructor is emitted once per exit path by counting `free` calls
in the generated IR (the leak is silent at runtime, so exit-code tests cannot catch
it). The analogous List<T> case shares the struct mechanism and is covered by the
.sushi corpus + a runtime `leaks` check.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


def test_array_freed_on_both_branch_returns(tmp_path):
    """if/else, each branch returns: the local array is freed on every path.

    There are at least two frees (the two branch returns). The compiler also emits
    an unreachable default-return at the merge block, which carries its own (dead)
    free -- so assert >= 2 rather than an exact count. The bug produced exactly one.
    """
    src = (
        "fn f(i32 t) i32:\n"
        "    let i32[] data = from([1, 2, 3])\n"
        "    if (t == 1):\n"
        "        return Result.Ok(1)\n"
        "    else:\n"
        "        return Result.Ok(2)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f(2).realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", 'call void @"free"')
    assert frees >= 2, f"array must be freed on both branch-return paths, got {frees}"


def test_array_single_return_freed_once(tmp_path):
    """A single-return function frees its local array exactly once (no double-free)."""
    src = (
        "fn f() i32:\n"
        "    let i32[] data = from([1, 2, 3])\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", 'call void @"free"')
    assert frees == 1, f"single-return array must be freed exactly once, got {frees}"


def test_array_freed_on_if_and_trailing_return(tmp_path):
    """An early return inside `if` plus a trailing return: both paths free."""
    src = (
        "fn f(i32 t) i32:\n"
        "    let i32[] data = from([1, 2, 3])\n"
        "    if (t == 1):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f(2).realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", 'call void @"free"')
    assert frees == 2, f"array must be freed on the if-return and trailing-return paths, got {frees}"


def test_array_freed_on_both_qq_paths(tmp_path):
    """`??` before a return: the error and success paths both free the local array."""
    src = (
        "fn g() i32:\n"
        "    return Result.Ok(5)\n"
        "\n"
        "fn f() i32:\n"
        "    let i32[] data = from([1, 2, 3])\n"
        "    let i32 x = g()??\n"
        "    return Result.Ok(x)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", 'call void @"free"')
    assert frees == 2, f"array must be freed on the ?? error path and the success-return path, got {frees}"


# NOTE: the analogous struct-with-dynamic-array-field case (and List<T>) is NOT fixed
# here. Those cleanups are entangled with a separate, pre-existing double-ownership
# bug: a struct copied out of an array (e.g. `let b = arr.get(0)??`) shallow-shares
# its array buffer with the array element, so freeing on every exit path double-frees.
# That must be fixed first; tracked separately. This file pins the dynamic-array (T[])
# case from #59, which uses move semantics and has no such aliasing.
