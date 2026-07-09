"""Regression tests for #147: string RAII for struct string fields.

A heap `string` stored in a struct field must be freed when the struct goes out of
scope, and every path that copies such a struct must clone the string field (clone-if-
owned) so exactly one owner frees each heap buffer -- no leak, no double-free.

Like test_struct_raii.py (#60), these assert behaviour by counting `malloc`/`free` in the
generated IR: the bug is a silent leak (missing free) / latent double-free (missing clone).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function


_STRUCT = (
    "use <collections/strings>\n"
    "\n"
    "struct P:\n"
    "    string name\n"
    "\n"
)

_FREE = '@"free"'


def test_struct_string_field_freed_at_scope_exit(tmp_path):
    """A struct local whose only owning field is a heap `string` frees it at scope exit.

    `"x".upper()` mallocs one heap buffer; it is stored in `p.name`. When `p` leaves
    `make`'s scope its string field must be freed. Before the fix the struct was never
    registered for cleanup (`struct_needs_cleanup` ignored strings) so `make` emitted
    zero frees and leaked the buffer.
    """
    src = _STRUCT + (
        "fn make() i32:\n"
        "    let P p = P(name: \"x\".upper())\n"
        "    return Result.Ok(p.name.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let i32 n = make().realise(0)\n"
        "    return Result.Ok(0)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    frees = _count_in_function(ir_text, "make", _FREE)
    assert frees >= 1, f"struct string field must be freed at scope exit, got {frees} frees"


def test_byvalue_struct_string_arg_cloned_at_call_site(tmp_path):
    """Passing a struct{string} by value clones its string field (independent buffer).

    The callee owns and frees its by-value copy's string at scope exit; without a call-site
    clone the caller's original and the callee's copy share one heap buffer and double-free
    (SIGABRT). The clone-if-owned helper emits a `malloc` directly in `main` (the `.upper()`
    buffer malloc lives inside the string runtime fn, not inlined here). Before the fix
    `deep_copy_struct` cloned only array/nested-struct fields, so `main` had zero mallocs.
    """
    src = _STRUCT + (
        "fn consume(P d) i32:\n"
        "    return Result.Ok(d.name.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let P x = P(name: \"hi\".upper())\n"
        "    let i32 n = consume(x).realise(0)\n"
        "    return Result.Ok(x.name.len())\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "user_main", "malloc")
    assert mallocs >= 1, f"call site must clone the struct arg's string field, got {mallocs} mallocs in main"


def test_ffi_char_return_copied_to_owned(tmp_path):
    """An FFI `char*` return is copied into a Sushi-owned buffer and freed at scope exit.

    Sushi never frees the foreign pointer, so the marshalling copies it (malloc + memcpy)
    and marks the result owned=1; the copy is then RAII-freed (`@"free"`) at scope exit, so
    there is no leak and the foreign buffer is left untouched. Before the fix the foreign
    pointer was wrapped in place (owned=0) and leaked.
    """
    src = (
        'unsafe external "C" as libc because "getenv returns a borrowed char*":\n'
        '    fn getenv(string name) string = "getenv"\n'
        '\n'
        'fn use_env() i32:\n'
        '    let string v = libc.getenv("HOME")\n'
        '    println(v)\n'
        '    return Result.Ok(0)\n'
        '\n'
        'fn main() i32:\n'
        '    let i32 n = use_env().realise(0)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "use_env", "malloc")
    frees = _count_in_function(ir_text, "use_env", _FREE)
    assert mallocs >= 1, f"FFI string return must copy the foreign buffer, got {mallocs} mallocs"
    assert frees >= 1, f"the owned copy of an FFI string return must be freed, got {frees} frees"
