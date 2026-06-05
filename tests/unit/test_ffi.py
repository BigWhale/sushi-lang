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
