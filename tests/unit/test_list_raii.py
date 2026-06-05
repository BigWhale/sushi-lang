"""Regression tests for #61: local List<T> variables are freed by RAII.

A local `List<T>` owns a heap buffer (allocated by `.push()` growth). Before the fix it
was never registered for automatic cleanup, so the buffer leaked on every path unless the
programmer called `.free()`/`.destroy()` -- unlike `T[]`, which is auto-freed.

These assert the destructor is emitted by counting `free` calls in the generated IR (the
leak is silent at runtime), mirroring tests/unit/test_raii_multi_exit.py for `T[]`.
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


def test_list_freed_on_single_return(tmp_path):
    """A single-return function frees its local List<T> at scope exit (RAII)."""
    src = (
        "fn f() i32:\n"
        "    let List<i32> data = List.new()\n"
        "    data.push(1)\n"
        "    data.push(2)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    assert frees >= 1, f"local List<T> must be freed at scope exit, got {frees} frees"


def test_list_freed_on_both_branch_returns(tmp_path):
    """if/else, each branch returns: the local List<T> is freed on every path."""
    src = (
        "fn f(i32 t) i32:\n"
        "    let List<i32> data = List.new()\n"
        "    data.push(1)\n"
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
    frees = _count_in_function(ir_text, "f", '@"free"')
    assert frees >= 2, f"List<T> must be freed on both branch-return paths, got {frees}"


def test_list_freed_on_if_and_trailing_return(tmp_path):
    """An early return inside `if` plus a trailing return: both paths free the List<T>."""
    src = (
        "fn f(i32 t) i32:\n"
        "    let List<i32> data = List.new()\n"
        "    data.push(1)\n"
        "    if (t == 1):\n"
        "        return Result.Ok(1)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f(2).realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    assert frees == 2, f"List<T> must be freed on the if-return and trailing-return paths, got {frees}"


def test_returned_list_not_freed_by_callee(tmp_path):
    """A List<T> returned to the caller is moved, not freed by the callee (no use-after-free).

    `make` builds a list and returns it; the caller owns it. The callee must NOT free the
    returned list's buffer. `make` therefore frees nothing (it has no other heap-owning
    locals).
    """
    src = (
        "fn make() List<i32>:\n"
        "    let List<i32> data = List.new()\n"
        "    data.push(1)\n"
        "    return Result.Ok(data)\n"
        "\n"
        "fn main() i32:\n"
        "    let List<i32> got = make()??\n"
        "    got.free()\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "make", '@"free"')
    assert frees == 0, f"a returned (moved) List<T> must not be freed by the callee, got {frees}"
