"""Regression tests for #147: string RAII for struct string fields."""
from __future__ import annotations

from tests.unit.test_ffi import _emit_ir, _count_in_function, _ensure_newline


def _analysis_codes(tmp_path, src: str) -> list[str]:
    """Run the front end + semantic analysis and return the diagnostic codes."""
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    from sushi_lang.semantics.units import UnitManager
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
    from sushi_lang.internals.parser import parse_to_ast

    text = _ensure_newline(src)
    (tmp_path / "main.sushi").write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)

    reporter = Reporter(source=text, filename="main")
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    assert unit_manager.load_unit("main", program) is not None
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
    """A struct local whose only owning field is a heap `string` frees it at scope exit."""
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
    """Passing a struct{string} by value MOVES it. The call site inserts no clone."""
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
    """The user-visible half of `nom`: reusing the value after the call is CE2405."""
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
    """The twin: an UNMARKED parameter borrows, so the same program is clean."""
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
    """An FFI `char*` return is copied into a Sushi-owned buffer and freed at scope exit."""
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
