"""Regression tests for #60: struct value-semantics for heap-owning structs.

A struct that owns heap memory (a dynamic-array `T[]` field) must get an *independent*
buffer whenever it is copied -- taken out of an array via `.get()`/indexing, or passed
by value to a function -- so that exactly one owner frees each allocation. Before the
fix the copy shallow-shared the element's buffer (double-ownership), and by-value struct
parameters were never freed by the callee at all.

These assert behaviour by counting `malloc`/`free` in the generated IR (the bug is a
silent leak / latent double-free at runtime, mirroring the #59 approach in
test_raii_multi_exit.py).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


_STRUCT = (
    "struct DataBuffer:\n"
    "    u8[] data\n"
    "    i32 size\n"
    "\n"
)


def test_struct_get_deep_copies_array_field(tmp_path):
    """`arr.get(i)??` of a struct with a `T[]` field clones the buffer (independent copy).

    `extract` takes the array by value and only does the `.get()`, so the sole heap
    allocation in `extract` is the deep copy of the extracted struct's array field. Before
    the fix the element was loaded as-is (shallow share) and `extract` had zero mallocs.
    """
    src = _STRUCT + (
        "fn extract(DataBuffer[] bufs) i32:\n"
        "    let DataBuffer b = bufs.get(0)??\n"
        "    return Result.Ok(b.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer[] buffers = from([DataBuffer(from([1 as u8, 2 as u8]), 2)])\n"
        "    let i32 n = extract(buffers).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "extract", "malloc")
    assert mallocs >= 1, f"`.get()` of a struct with a T[] field must deep-copy the buffer, got {mallocs} mallocs"


def test_struct_index_deep_copies_array_field(tmp_path):
    """`arr[i]` of a struct with a `T[]` field clones the buffer (independent copy)."""
    src = _STRUCT + (
        "fn extract(DataBuffer[] bufs) i32:\n"
        "    let DataBuffer b = bufs[0]\n"
        "    return Result.Ok(b.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer[] buffers = from([DataBuffer(from([1 as u8, 2 as u8]), 2)])\n"
        "    let i32 n = extract(buffers).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "extract", "malloc")
    assert mallocs >= 1, f"`arr[i]` of a struct with a T[] field must deep-copy the buffer, got {mallocs} mallocs"


def test_byvalue_struct_param_freed_by_callee(tmp_path):
    """A by-value struct param with a `T[]` field is freed by the callee at scope exit.

    The callee owns its own (deep-copied) copy, so it must free it. Before the fix struct
    parameters were never registered for cleanup and the callee emitted zero frees.
    """
    src = _STRUCT + (
        "fn consume(DataBuffer d) i32:\n"
        "    return Result.Ok(d.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer x = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let i32 n = consume(x).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "consume", '@"free"')
    assert frees >= 1, f"callee must free its by-value struct param's buffer, got {frees} frees"


def test_byvalue_struct_arg_deep_copied_at_call_site(tmp_path):
    """The call site deep-copies a struct arg with a `T[]` field (independent copy).

    Pairs with test_byvalue_struct_param_freed_by_callee: the caller clones the arg so the
    callee's free does not double-free the caller's original. The clone is the only heap
    allocation in the call expression's block of `main` beyond the struct construction.
    """
    src = _STRUCT + (
        "fn consume(DataBuffer d) i32:\n"
        "    return Result.Ok(d.data.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let DataBuffer x = DataBuffer(from([1 as u8, 2 as u8, 3 as u8]), 3)\n"
        "    let i32 n = consume(x).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    # main allocates the struct's array once (from([...])); the call-site deep copy adds
    # a second malloc. Before the fix main had exactly one (the construction).
    mallocs = _count_in_function(ir_text, "user_main", "malloc")
    assert mallocs >= 2, f"call site must deep-copy the struct arg's buffer, got {mallocs} mallocs in main"


def test_struct_local_freed_on_every_branch_return(tmp_path):
    """if/else, each branch returns: a struct-with-array-field local is freed on every path.

    Completes the #59 every-path RAII for struct-with-array-field locals (was blocked by
    the #60 aliasing). Before the fix the global-once gate emitted the struct cleanup on
    only one path.
    """
    src = _STRUCT + (
        "fn f(i32 t) i32:\n"
        "    let DataBuffer d = DataBuffer(from([1 as u8, 2 as u8]), 2)\n"
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
    assert frees >= 2, f"struct-with-array-field local must be freed on both branch returns, got {frees}"
