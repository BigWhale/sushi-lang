"""Regression tests for #141: println / print must not leak heap memory.

Two distinct leak sources were confirmed by isolating with `leaks --atExit`:

1. Print-path C-string temporaries. `emit_print_value` converted the fat-pointer
   string to a null-terminated C string via `emit_to_cstr` (a malloc) for printf,
   and built a "\\n" C string the same way for println -- neither was ever freed.
   Present in EVERY string print (literal or variable), ~32 bytes. The fix prints
   the fat pointer directly with `printf("%.*s", size, data)` (and a "\\n" format for
   the newline): zero allocation, nothing to free.

2. Interpolation temporaries. Building "x={x}" allocates an int-to-string buffer and
   a concatenation buffer; both leaked (independent of println, ~80 bytes). The fix
   frees the interpolation temporaries once the printed value has been consumed.

These assert behaviour by counting malloc/free in the generated IR (mirroring the
#59/#60 approach in test_raii_multi_exit.py / test_struct_raii.py).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function

_MALLOC = 'call i8* @"malloc"'
_FREE = 'call void @"free"'


def test_println_string_var_allocates_nothing(tmp_path):
    """`println(s)` for a string variable emits no malloc (no C-string copy for printf).

    Before the fix the print path malloc'd twice (the value's C-string copy and the
    "\\n" C-string copy). `%.*s` prints the fat pointer in place, so the print of a
    global-backed string variable allocates nothing at all.
    """
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
    """`println("x={x}")` frees every heap temporary it allocates (malloc == free).

    The interpolation builds an int-to-string buffer and a concat buffer; both must be
    freed once printed. Before the fix the function had unbalanced malloc/free (frees=0).
    """
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
