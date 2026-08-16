"""Regression tests for #147: string RAII for struct string fields.

A heap `string` stored in a struct field must be freed when the struct goes out of
scope, and every path that copies such a struct must clone the string field (clone-if-
owned) so exactly one owner frees each heap buffer -- no leak, no double-free.

Like test_struct_raii.py (#60), these assert behaviour by counting `malloc`/`free` in the
generated IR: the bug is a silent leak (missing free) / latent double-free (missing clone).
"""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function, _ensure_newline


def _analysis_codes(tmp_path, src: str) -> list[str]:
    """Run the front end + semantic analysis and return the diagnostic codes.

    `_emit_ir` asserts the program is clean, so it cannot be used to check a program that is
    MEANT to be rejected. This stops one step earlier and hands back what was reported.
    """
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.generics.active_generics import reset_active_generics
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    from sushi_lang.semantics.units import UnitManager
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
    from sushi_lang.internals.parser import parse_to_ast

    text = _ensure_newline(src)
    (tmp_path / "main.sushi").write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)

    reporter = Reporter(source=text, filename="main")
    reset_active_generics()
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    assert unit_manager.load_unit("main", program) is not None
    unit_manager.build_global_symbol_table()
    unit_manager.get_compilation_order()

    SemanticAnalyzer(reporter, filename="main", unit_manager=unit_manager).check(program)
    return [i.code for i in reporter.items]


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


def test_byvalue_struct_string_arg_moves_at_call_site(tmp_path):
    """Passing a struct{string} by value MOVES it. The call site inserts no clone.

    **Inverted by Phase 9.** This test used to assert the opposite -- that the call site
    CLONED the string field, so the caller and callee each owned an independent buffer and
    the caller could keep using its value. That was the COPY tier, and the tier is gone: a
    struct whose only owning content is a string now MOVES like any other owning value.

    So `main` is back to ZERO mallocs, which is what it emitted before #147 added the
    call-site clone -- but for the opposite reason. Then there was no clone because the
    struct copy path ignored string fields (a latent double free). Now there is no clone
    because nothing is copied at all: the callee takes ownership and frees it once.

    The `.upper()` buffer malloc lives inside the string runtime fn, not inlined here, so it
    does not show up in this count either way.

    The user-visible half of the change is the companion test below: reusing the value after
    the call is now CE2405.
    """
    src = _STRUCT + (
        "fn consume(P d) i32:\n"
        "    return Result.Ok(d.name.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let P x = P(name: \"hi\".upper())\n"
        "    let i32 n = consume(x).realise(0)\n"
        "    return Result.Ok(n)\n"
    )
    ir_text = _emit_ir(tmp_path, src)
    mallocs = _count_in_function(ir_text, "user_main", "malloc")
    assert mallocs == 0, (
        f"the call site must MOVE the struct arg, not clone it, got {mallocs} mallocs in main"
    )


def test_nom_struct_string_arg_is_use_after_move(tmp_path):
    """The user-visible half of `nom`: reusing the value after the call is CE2405.

    This is the whole point of deleting the COPY tier. Before Phase 9 this program compiled,
    because the call site cloned `x`'s string field and the caller kept an independent buffer.
    A `nom` parameter takes ownership, so reading `x.name` afterwards is a use-after-move.

    `.clone()` at the call site is the escape, which is what the diagnostic says.
    """
    src = _STRUCT + (
        "fn consume(nom P d) i32:\n"
        "    return Result.Ok(d.name.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let P x = P(name: \"hi\".upper())\n"
        "    let i32 n = consume(nom x).realise(0)\n"
        "    return Result.Ok(x.name.len())\n"
    )
    assert "CE2405" in _analysis_codes(tmp_path, src)


def test_borrow_struct_string_arg_stays_usable(tmp_path):
    """The twin: an UNMARKED parameter borrows, so the same program is clean.

    One word separates the two files' programs, and it is the word the reader can see at
    the call site (docs/design/borrow-model.md S3).
    """
    src = _STRUCT + (
        "fn look(P d) i32:\n"
        "    return Result.Ok(d.name.len())\n"
        "\n"
        "fn main() i32:\n"
        "    let P x = P(name: \"hi\".upper())\n"
        "    let i32 n = look(x).realise(0)\n"
        "    return Result.Ok(x.name.len())\n"
    )
    assert "CE2405" not in _analysis_codes(tmp_path, src)


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
