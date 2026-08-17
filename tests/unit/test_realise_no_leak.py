"""Regression tests for `realise(default)` ownership when `T` owns heap (#159 and follow-ons).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function

_MALLOC = 'call i8* @"malloc"'
_FREE = 'call void @"free"'

_STRUCT = "struct Buf:\n    i32[] data\n\n"

# Payload wins; the default is a fresh temporary the call adopts and must destroy.
_ADOPTED_DEFAULT = _STRUCT + (
    "fn run() i32:\n"
    "    let Buf[] arr = from([Buf(data: from([1, 2, 3]))])\n"
    "    let Buf first = arr.get(0).realise(Buf(data: from([9])))\n"
    "    return Result.Ok(first.data.len())\n"
    "\n"
    "fn main() i32:\n"
    "    let i32 n = run().realise(0)\n"
    "    return Result.Ok(0)\n"
)

# Default wins; it is a named binding that stays live, so it must be cloned, not aliased.
_BORROWED_DEFAULT = _STRUCT + (
    "fn run() i32:\n"
    "    let Buf[] arr = from([Buf(data: from([1, 2, 3]))])\n"
    "    let Buf d = Buf(data: from([9]))\n"
    "    let Buf first = arr.get(99).realise(d)\n"
    "    return Result.Ok(first.data.len())\n"
    "\n"
    "fn main() i32:\n"
    "    let i32 n = run().realise(0)\n"
    "    return Result.Ok(0)\n"
)


def test_adopted_default_temporary_is_destroyed(tmp_path):
    """The discarded default temporary is freed when the payload wins (the #159 leak)."""
    ir_text = _emit_ir(tmp_path, _ADOPTED_DEFAULT)
    mallocs = _count_in_function(ir_text, "run", _MALLOC)
    frees = _count_in_function(ir_text, "run", _FREE)
    assert frees > mallocs, (
        f"realise must destroy the default temporary it discarded, got {frees} frees "
        f"for {mallocs} mallocs (the discarded default leaks)"
    )


def test_borrowed_default_is_cloned_not_aliased(tmp_path):
    """A named default is deep-copied before being returned, so it is not freed twice."""
    borrowed = _count_in_function(_emit_ir(tmp_path, _BORROWED_DEFAULT), "run", _MALLOC)
    adopted = _count_in_function(_emit_ir(tmp_path, _ADOPTED_DEFAULT), "run", _MALLOC)
    assert borrowed == adopted + 1, (
        "a borrowed (named) default must be cloned before it is returned, expected one more "
        f"malloc than the adopted-temporary case ({adopted}), got {borrowed}"
    )


def test_copyable_payload_keeps_the_select_fast_path(tmp_path):
    """A `T` that owns no heap is untouched: no clone, no destructor, no extra free."""
    src = (
        "fn run() i32:\n"
        "    let i32[] arr = from([1, 2, 3])\n"
        "    let i32 first = arr.get(0).realise(7)\n"
        "    return Result.Ok(first)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 n = run().realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "run", _MALLOC)
    frees = _count_in_function(ir_text, "run", _FREE)
    assert frees == mallocs, (
        f"realise on a copyable payload must not add clones or destructors, "
        f"got {mallocs} mallocs / {frees} frees"
    )
