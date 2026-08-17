"""Regression tests for #141: println / print must not leak heap memory."""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function

_MALLOC = 'call i8* @"malloc"'
_FREE = 'call void @"free"'


def test_println_string_var_allocates_nothing(tmp_path):
    """`println(s)` for a string variable emits no malloc (no C-string copy for printf)."""
    src = (
        "fn emit_line(string s) i32:\n"
        "    println(s)\n"
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        '    let i32 r = emit_line("hello").realise(0)\n'
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "emit_line", _MALLOC)
    assert mallocs == 0, f"println of a string variable must not allocate, got {mallocs} mallocs"


def test_println_interpolation_frees_temporaries(tmp_path):
    """`println("x={x}")` frees every heap temporary it allocates (malloc == free)."""
    src = (
        "fn emit_interp(i32 x) i32:\n"
        '    println("x={x}")\n'
        "    return Result.Ok(0)\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 r = emit_interp(60).realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "emit_interp", _MALLOC)
    frees = _count_in_function(ir_text, "emit_interp", _FREE)
    assert mallocs >= 1, f"interpolation should allocate at least one temporary, got {mallocs}"
    assert mallocs == frees, (
        f"interpolation temporaries leak: {mallocs} mallocs but {frees} frees in emit_interp"
    )
