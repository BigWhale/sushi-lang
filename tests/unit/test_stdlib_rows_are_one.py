"""Every stdlib return type comes from ONE table per module, behind the unit's ladder (#798).

The instantiate pass and the back end each kept a hand-written list of stdlib function
names beside the files and socket tables. The instantiate list was read BEFORE the
unit's own declarations, so a user generic named `now`, `setenv` or `copy` could not be
called (CE2061). Now every registry module that answers a Result keeps a signature
table, the readers take the row from `stdlib_registry.stdlib_signature`, and a row
answers only when no declaration of the unit takes the name.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from sushi_lang.semantics.stdlib_registry import (
    signature_tables,
    stdlib_signature,
)

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"

READERS = {
    ROOT / "semantics" / "generics" / "instantiate" / "expressions.py": set(),
}

REGISTRY_MODULES = {
    "io/files": ("sushi_lang.sushi_stdlib.src.io.files_funcs", "files"),
}

def _every_row_name() -> set[str]:
    return {name for table in signature_tables().values() for name in table}


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_the_row_names_are_measured():
    """The control: the tables hold the names the old lists spelled."""
    names = _every_row_name()
    for spelled in ("copy", "file_size", "fd_readln"):
        assert spelled in names


@pytest.mark.parametrize("path", sorted(READERS), ids=lambda p: p.name)
def test_no_reader_spells_a_stdlib_function_name(path):
    spelled = sorted(_string_constants(path) & _every_row_name() - READERS[path])
    assert not spelled, f"{path.name} spells stdlib names outside a table: {spelled}"


@pytest.mark.parametrize("module_path", sorted(REGISTRY_MODULES))
def test_a_registry_module_reads_its_own_table(module_path):
    from sushi_lang.semantics.stdlib_registry import get_stdlib_registry

    table = signature_tables()[module_path]
    registry = get_stdlib_registry()

    assert table
    for name, sig in table.items():
        record = registry.get_function(module_path, name)
        assert record is not None
        assert record.get_return_type([param.ty for param in sig.params]) == sig.return_type()
        assert stdlib_signature(module_path, name) is sig
    assert registry.get_function(module_path, "mostly_harmless") is None


def _generated_rows():
    from sushi_lang.semantics.stdlib_registry import stdlib_symbol

    for module_path in GENERATORS:
        for name, sig in signature_tables()[module_path].items():
            yield module_path, stdlib_symbol(module_path, name), sig


def test_every_row_declares_the_function_its_generator_built():
    """The emitter declares each generated function from its row, and the declaration
    byte-matches what the generator built."""
    import importlib

    from sushi_lang.backend.expressions.calls.stdlib.signatures import (
        llvm_function_type,
    )

    generated = {}
    for python_path, _prefix in GENERATORS.values():
        module = importlib.import_module(python_path).generate_module_ir()
        generated.update({f.name: f.function_type for f in module.functions})

    checked = 0
    for module_path, symbol, sig in _generated_rows():
        assert symbol in generated, f"{module_path}: no generated {symbol}"
        assert str(llvm_function_type(sig)) == str(generated[symbol]), symbol
        checked += 1
    assert checked >= 32


# Every registry module's generator, and the prefix its generated symbols carry.
GENERATORS = {
    "io/files": ("sushi_lang.sushi_stdlib.src.io.files", "sushi_io_files_"),
    "net/socket": ("sushi_lang.sushi_stdlib.src.net", "sushi_net_"),
}


def _row_symbols(module_path: str, prefix: str) -> set[str]:
    return {f"{prefix}{name}" for name in signature_tables()[module_path]}


def _rowless(module_path: str, module) -> list[str]:
    """The functions a generated module DEFINES that no row names."""
    _python_path, prefix = GENERATORS[module_path]
    defined = {f.name for f in module.functions if not f.is_declaration}
    return sorted(defined - _row_symbols(module_path, prefix))


def test_every_registry_module_has_a_generator():
    assert set(GENERATORS) == set(signature_tables())


@pytest.mark.parametrize("module_path", sorted(GENERATORS))
def test_every_generated_function_has_a_row(module_path):
    """The missing direction: a generated function no row names cannot be called."""
    import importlib

    module = importlib.import_module(GENERATORS[module_path][0]).generate_module_ir()
    assert [f for f in module.functions if not f.is_declaration]
    assert not _rowless(module_path, module), (
        f"{module_path} generates functions with no row: {_rowless(module_path, module)}")


def test_a_generated_function_with_no_row_is_found():
    """The control: a function the generator adds and no row names is reported."""
    import importlib

    from llvmlite import ir

    module = importlib.import_module(GENERATORS["io/files"][0]).generate_module_ir()
    orphan = ir.Function(module, ir.FunctionType(ir.VoidType(), []),
                         "sushi_io_files_mostly_harmless")
    orphan.append_basic_block().name = "entry"
    ir.IRBuilder(orphan.blocks[0]).ret_void()
    assert _rowless("io/files", module) == ["sushi_io_files_mostly_harmless"]


def _scanner(generic_funcs=None, func_table=None):
    from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner

    tables = SimpleNamespace(struct_table={}, enum_table={},
                               func_table=func_table or {}, variable_types={})
    return ExpressionScanner(tables, set(), set(), generic_funcs or {})


def test_a_user_generic_takes_the_name_before_the_row():
    assert _scanner()._stdlib_row("file_size") is not None
    assert _scanner(generic_funcs={"file_size": object()})._stdlib_row("file_size") is None
    assert _scanner(func_table={"copy": object()})._stdlib_row("copy") is None
