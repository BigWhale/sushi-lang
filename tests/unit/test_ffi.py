"""Unit tests for FFI internals that the .sushi corpus cannot reach directly.

Covers:
- CE5002: a public function exposing a foreign `ptr` aborts the .slib manifest.
- The no-leak property: a marshalled char* is freed in the scope-cleanup path.
- RESERVED_EXTERNS stays in sync with the symbols declare_externs actually declares.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.units import Unit


def _ensure_newline(src: str) -> str:
    return src if src.endswith("\n") else src + "\n"


def _make_unit(tmp_path, src: str, name: str = "main") -> Unit:
    text = _ensure_newline(src)
    file_path = tmp_path / f"{name}.sushi"
    file_path.write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)
    return Unit(name=name, file_path=file_path, ast=program,
                dependencies=[], public_symbols={})


class _StubAnalyzer:
    """Minimal analyzer surface for LibraryManifestGenerator."""
    def __init__(self, reporter, structs, enums):
        self.reporter = reporter
        self.structs = structs
        self.enums = enums


def test_ce5002_public_foreign_ptr_aborts_manifest(tmp_path):
    """A public function returning `ptr` must abort the .slib write with CE5002."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable

    src = (
        'unsafe external "C" as libc because "bootstrap":\n'
        '    fn malloc(i64 n) ptr = "malloc"\n'
        '\n'
        'public fn make_handle(i64 n) ptr:\n'
        '    return Result.Ok(libc.malloc(n))\n'
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="main")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter, StructTable(), EnumTable()))

    with pytest.raises(ValueError):
        gen._extract_public_functions([unit])

    assert any(item.code == "CE5002" for item in reporter.items)


def test_ce5002_allows_ptr_free_function(tmp_path):
    """A NON-public ptr function is fine; only the public API is restricted."""
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable

    src = (
        'unsafe external "C" as libc because "bootstrap":\n'
        '    fn malloc(i64 n) ptr = "malloc"\n'
        '\n'
        'public fn add(i32 a, i32 b) i32:\n'
        '    return Result.Ok(a + b)\n'
    )
    unit = _make_unit(tmp_path, src)
    reporter = Reporter(source="", filename="main")
    gen = LibraryManifestGenerator(_StubAnalyzer(reporter, StructTable(), EnumTable()))

    funcs = gen._extract_public_functions([unit])
    assert [f["name"] for f in funcs] == ["add"]
    assert not any(item.code == "CE5002" for item in reporter.items)


def test_string_marshalling_frees_cstr_in_ir(tmp_path):
    """The marshalled char* for a string-param external is freed (no leak)."""
    src = (
        'unsafe external "C" as libc because "len":\n'
        '    fn strlen(string s) i64 = "strlen"\n'
        '\n'
        'fn length(string s) i64:\n'
        '    return Result.Ok(libc.strlen(s))\n'
        '\n'
        'fn main() i32:\n'
        '    let i64 n = length("Mostly Harmless").realise(0 as i64)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)

    # One malloc for the marshalled copy, the strlen call, and a matching free.
    # (Names are quoted and pointers spelled i8* in the unoptimized module.)
    assert 'call i8* @"malloc"' in ir_text
    assert 'call i64 @"strlen"' in ir_text
    assert 'call void @"free"' in ir_text
    # The free of the marshalled char* must appear after the strlen call
    # (i.e. in the scope-cleanup path), proving no leak.
    assert ir_text.index('call void @"free"') > ir_text.index('call i64 @"strlen"')


def _function_body(ir_text: str, fn_name: str) -> str:
    """Return the IR text of the body of `define ... @fn_name(...)`.

    The body brace is the `{` that opens the block (spelled `{\\n` by llvmlite),
    NOT an earlier `{` belonging to a struct return/parameter type.
    """
    start = ir_text.index(f'define ')
    while True:
        # Find a `define` whose name matches `@"fn_name"(`.
        marker = f'@"{fn_name}"('
        def_at = ir_text.index('define', start)
        line_end = ir_text.index("\n", def_at)
        if marker in ir_text[def_at:line_end]:
            break
        start = line_end
    brace = ir_text.index("{\n", def_at)
    depth = 0
    end = brace
    for i in range(brace, len(ir_text)):
        if ir_text[i] == "{":
            depth += 1
        elif ir_text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    return ir_text[brace:end]


def _count_in_function(ir_text: str, fn_name: str, needle: str) -> int:
    """Count occurrences of `needle` inside the body of `@fn_name` only."""
    return _function_body(ir_text, fn_name).count(needle)


def test_string_marshalling_no_leak_multi_return(tmp_path):
    """Multi-exit function: every return path frees the marshalled char* exactly once.

    Regression for the bug where the first emitted exit block drained-and-cleared
    the shared cstr registry, so every later mutually-exclusive exit path emitted
    NO free and leaked. The fix emits a free in EVERY exit block (basic blocks are
    mutually exclusive at runtime), so malloc-count == free-count in the function
    and BOTH the early-return block and the fall-through block carry a free.
    """
    src = (
        'unsafe external "C" as libc because "len":\n'
        '    fn strlen(string s) i64 = "strlen"\n'
        '\n'
        'fn work(string s, bool flag) i64:\n'
        '    let i64 n = libc.strlen(s)\n'
        '    if (flag):\n'
        '        return Result.Ok(n)\n'
        '    return Result.Ok(0 as i64)\n'
        '\n'
        'fn main() i32:\n'
        '    let i64 r = work("hi", true).realise(0 as i64)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)

    mallocs = _count_in_function(ir_text, "work", 'call i8* @"malloc"')
    frees = _count_in_function(ir_text, "work", 'call void @"free"')
    assert mallocs == 1, f"expected exactly one marshalling malloc, got {mallocs}"
    # One free per mutually-exclusive exit block: the early `if (flag)` return AND
    # the fall-through return. Exactly one runs at runtime, so this is no double free.
    assert frees == 2, (
        f"expected a free in EACH of the two exit blocks, got {frees}; "
        "the early-return path is leaking the marshalled char*"
    )

    # The early-return block (if.0.body) must itself contain a free, not only the
    # fall-through. Locate that block and assert a free precedes its ret.
    work_body = _function_body(ir_text, "work")
    body_idx = work_body.index("if.0.body")
    next_block = work_body.index("ret ", body_idx)
    early_block = work_body[body_idx:next_block]
    assert 'call void @"free"' in early_block, (
        "the early `if (flag)` return block has no free of the marshalled char*"
    )


def test_string_marshalling_no_leak_try_success_path(tmp_path):
    """A `??` after a marshalled call: BOTH the propagate block and the success
    continuation free the marshalled char* (the common success path must not leak).
    """
    src = (
        'enum MyErr:\n'
        '    Bad\n'
        '\n'
        'unsafe external "C" as libc because "len":\n'
        '    fn strlen(string s) i64 = "strlen"\n'
        '\n'
        'fn helper(i64 n) i64 | MyErr:\n'
        '    if (n > (100 as i64)):\n'
        '        return Result.Err(MyErr.Bad())\n'
        '    return Result.Ok(n)\n'
        '\n'
        'fn work(string s) i64 | MyErr:\n'
        '    let i64 n = libc.strlen(s)\n'
        '    let i64 m = helper(n)??\n'
        '    return Result.Ok(m)\n'
        '\n'
        'fn main() i32:\n'
        '    let i64 r = work("hi").realise(0 as i64)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)

    mallocs = _count_in_function(ir_text, "work", 'call i8* @"malloc"')
    frees = _count_in_function(ir_text, "work", 'call void @"free"')
    assert mallocs == 1, f"expected one marshalling malloc, got {mallocs}"
    # One free on the `??` error-propagation path and one on the success
    # continuation (the common path) -> two mutually-exclusive frees.
    assert frees == 2, (
        f"expected a free on both the ?? propagate and success paths, got {frees}; "
        "the success continuation is leaking the marshalled char*"
    )

    # The success continuation block must carry a free.
    work_body = _function_body(ir_text, "work")
    cont_idx = work_body.index("try_continue")
    cont_ret = work_body.index("ret ", cont_idx)
    cont_block = work_body[cont_idx:cont_ret]
    assert 'call void @"free"' in cont_block, (
        "the ?? success continuation has no free of the marshalled char*"
    )


def test_variadic_extern_declared_var_arg(tmp_path):
    """A variadic external lowers to an LLVM `var_arg` declaration."""
    src = (
        'unsafe external "C" as libc because "printf":\n'
        '    fn printf(string fmt, ...) i32 = "printf"\n'
        '\n'
        'fn shout(string s) i32:\n'
        '    return Result.Ok(libc.printf("%s\\n", s))\n'
        '\n'
        'fn main() i32:\n'
        '    let i32 r = shout("hi").realise(0)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)
    # llvmlite spells a var_arg declaration with a trailing `, ...)`.
    assert 'declare i32 @"printf"(i8* %".1", ...)' in ir_text


def test_variadic_string_arg_freed_no_leak(tmp_path):
    """A `string` passed as a VARIADIC argument is marshalled to char* and freed
    on every exit path (no leak), exactly like a fixed string argument.
    """
    src = (
        'unsafe external "C" as libc because "printf":\n'
        '    fn printf(string fmt, ...) i32 = "printf"\n'
        '\n'
        'fn emit(string s, bool flag) i32:\n'
        '    let i32 n = libc.printf("%s\\n", s)\n'
        '    if (flag):\n'
        '        return Result.Ok(n)\n'
        '    return Result.Ok(0)\n'
        '\n'
        'fn main() i32:\n'
        '    let i32 r = emit("hi", true).realise(0)\n'
        '    return Result.Ok(0)\n'
    )
    ir_text = _emit_ir(tmp_path, src)

    # Two strings are marshalled in `emit`: the fixed format string and the
    # VARIADIC string `s`. Both must be freed on EVERY mutually-exclusive exit
    # block (the early `if (flag)` return and the fall-through return), so the
    # free count is exactly 2 marshalled strings x 2 exit paths == 4. Without the
    # variadic char* registration the count would drop to 2.
    mallocs = _count_in_function(ir_text, "emit", 'call i8* @"malloc"')
    frees = _count_in_function(ir_text, "emit", 'call void @"free"')
    assert mallocs == 2, f"expected two marshalling mallocs (fmt + variadic), got {mallocs}"
    assert frees == 2 * mallocs, (
        f"expected every marshalled char* freed on every exit path "
        f"({2 * mallocs}), got {frees}; a variadic-marshalled char* is leaking"
    )

    # The early-return block must itself carry frees for BOTH marshalled strings,
    # not only the fall-through (proves the variadic char* is registered).
    emit_body = _function_body(ir_text, "emit")
    body_idx = emit_body.index("if.0.body")
    next_block = emit_body.index("ret ", body_idx)
    early_block = emit_body[body_idx:next_block]
    assert early_block.count('call void @"free"') == mallocs, (
        "the early `if (flag)` return block does not free every marshalled "
        "char* (including the variadic one)"
    )


def test_reserved_externs_are_declared():
    """Every RESERVED_EXTERNS name must actually be declared by the backend."""
    from llvmlite import ir
    from sushi_lang.backend.codegen_llvm import LLVMCodegen
    from sushi_lang.backend.runtime.core import RESERVED_EXTERNS

    cg = LLVMCodegen(module_name="reserved_sync")
    cg.runtime.declare_externs()
    # malloc/free/realloc/exit are declared lazily; force them so the manifest is
    # checked against the full built-in extern surface.
    cg.get_malloc_func()
    cg.get_free_func()
    cg.get_realloc_func()
    # exit is declared by libc_process.declare_all (already run via declare_externs).

    declared = {
        name for name, g in cg.module.globals.items()
        if isinstance(g, ir.Function)
    }
    # The compiler provides `strlen` via an inlined LLVM IR intrinsic named
    # `llvm_strlen` (see libc_strings._declare_strlen); it reserves the C symbol
    # `strlen` for clash detection all the same.
    aliases = {"strlen": "llvm_strlen"}
    missing = [
        name for name in RESERVED_EXTERNS
        if name not in declared and aliases.get(name) not in declared
    ]
    assert not missing, f"RESERVED_EXTERNS not declared by the backend: {missing}"


def _emit_ir(tmp_path, src: str) -> str:
    """Compile `src` to LLVM IR text via the production multi-file pipeline."""
    from sushi_lang.semantics.generics.providers import register_all_providers
    from sushi_lang.semantics.generics.providers.registry import GenericTypeRegistry
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
    from sushi_lang.semantics.units import UnitManager
    from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
    from sushi_lang.backend.codegen_llvm import LLVMCodegen

    text = _ensure_newline(src)
    file_path = tmp_path / "main.sushi"
    file_path.write_text(text, encoding="utf-8")
    program, _tree = parse_to_ast(text)

    reporter = Reporter(source=text, filename="main")
    register_all_providers()
    GenericTypeRegistry.deactivate_all()
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    unit = unit_manager.load_unit("main", program)
    assert unit is not None
    unit_manager.build_global_symbol_table()
    order = unit_manager.get_compilation_order()

    analyzer = SemanticAnalyzer(reporter, filename="main", unit_manager=unit_manager)
    analyzer.check(program)
    assert not reporter.has_errors, [i.code for i in reporter.items]

    cg = LLVMCodegen(struct_table=analyzer.structs, enum_table=analyzer.enums,
                     func_table=analyzer.funcs, perk_impl_table=analyzer.perk_impls,
                     const_table=analyzer.constants)
    cg.external_table = analyzer.externals
    module = cg.build_module_multi_unit(order)
    return str(module)
