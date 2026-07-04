"""Regression tests: break/continue free heap-owning loop-body locals (RAII).

`emit_break`/`emit_continue` branch straight to the loop end/condition block. Because
that terminates the current block, the loop-body `pop_scope` skips its destructor
emission, so a heap-owning local declared in the loop body (a `List<T>`, a `T[]`)
abandoned via `break`/`continue` was never freed -- a silent leak on that path only.

These count `free` calls in the generated IR (the leak is invisible at runtime),
mirroring test_list_raii.py. A `while` loop is used rather than a range `foreach`
because a literal range compiles to both an ascending and a descending loop, which
would double the free count and mask the missing break-path free. They also guard the
inverse: a local that lives *past* the loop must NOT be freed on the break path (that
would be a double free).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


def test_list_freed_on_break(tmp_path):
    """A List<T> declared in a loop body is freed on the break path and the normal path."""
    src = (
        "fn f() i32:\n"
        "    let i32 i = 0\n"
        "    while (i < 3):\n"
        "        let List<i32> data = List.new()\n"
        "        data.push(i)\n"
        "        if (i == 1):\n"
        "            break\n"
        "        i := i + 1\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    # One free for the normal end-of-iteration path, one for the break path.
    assert frees >= 2, f"loop-body List<T> must be freed on the break path too, got {frees}"


def test_list_freed_on_continue(tmp_path):
    """A List<T> declared in a loop body is freed on the continue path and the normal path."""
    src = (
        "fn f() i32:\n"
        "    let i32 i = 0\n"
        "    while (i < 3):\n"
        "        let List<i32> data = List.new()\n"
        "        data.push(i)\n"
        "        i := i + 1\n"
        "        if (i == 1):\n"
        "            continue\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    # One free for the fall-through end-of-iteration path, one for the continue path.
    assert frees >= 2, f"loop-body List<T> must be freed on the continue path too, got {frees}"


def test_dynamic_array_freed_on_break(tmp_path):
    """A T[] declared in a loop body is freed on the break path."""
    src = (
        "fn f() i32:\n"
        "    let i32 i = 0\n"
        "    while (i < 3):\n"
        "        let i32[] buf = from([1, 2, 3])\n"
        "        if (i == 1):\n"
        "            break\n"
        "        i := i + 1\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    assert frees >= 2, f"loop-body T[] must be freed on the break path too, got {frees}"


def test_outer_local_not_double_freed_on_break(tmp_path):
    """A List<T> that outlives the loop is freed once (at return), never on the break path."""
    src = (
        "fn f() i32:\n"
        "    let List<i32> outer = List.new()\n"
        "    outer.push(0)\n"
        "    let i32 i = 0\n"
        "    while (i < 3):\n"
        "        if (i == 1):\n"
        "            break\n"
        "        i := i + 1\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = f().realise(-1)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "f", '@"free"')
    # `outer` outlives the loop: the break must clean only the (empty) loop-body scope,
    # not the enclosing scope, so `outer` is freed exactly once at the return.
    assert frees == 1, f"outer List<T> must be freed exactly once, got {frees} (double free on break?)"
