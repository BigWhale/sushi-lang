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
    _get_param_specs,
    signature_tables,
    stdlib_signature,
)

ROOT = Path(__file__).resolve().parents[2] / "sushi_lang"
READERS = (
    ROOT / "semantics" / "generics" / "instantiate" / "expressions.py",
    ROOT / "backend" / "expressions" / "calls" / "utils.py",
)

REGISTRY_MODULES = {
    "time": ("sushi_lang.sushi_stdlib.src.time", "time"),
    "sys/env": ("sushi_lang.sushi_stdlib.src.sys.env", "env"),
    "sys/process": ("sushi_lang.sushi_stdlib.src.sys.process", "process"),
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
    for spelled in ("sleep", "now", "monotonic_ns", "setenv", "getenv", "chdir",
                    "getcwd", "run", "copy", "remove", "fd_readln"):
        assert spelled in names


@pytest.mark.parametrize("path", READERS, ids=lambda p: p.name)
def test_no_reader_spells_a_stdlib_function_name(path):
    spelled = sorted(_string_constants(path) & _every_row_name())
    assert not spelled, f"{path.name} spells stdlib names outside a table: {spelled}"


@pytest.mark.parametrize("module_path", sorted(REGISTRY_MODULES))
def test_a_registry_module_reads_its_own_table(module_path):
    import importlib

    python_path, short = REGISTRY_MODULES[module_path]
    module = importlib.import_module(python_path)
    table = signature_tables()[module_path]
    checker = getattr(module, f"is_builtin_{short}_function")
    returns = getattr(module, f"get_builtin_{short}_function_return_type")
    specs = _get_param_specs()

    assert table
    for name, sig in table.items():
        assert checker(name)
        assert returns(name) == sig.return_type()
        assert specs[(short, name)] == [param.ty for param in sig.params]
        assert stdlib_signature(module_path, name) is sig
    assert not checker("mostly_harmless")


def _scanner(generic_funcs=None, func_table=None):
    from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner

    inferrer = SimpleNamespace(struct_table={}, enum_table={},
                               func_table=func_table or {}, variable_types={})
    return ExpressionScanner(inferrer, set(), set(), generic_funcs or {})


def test_a_user_generic_takes_the_name_before_the_row():
    assert _scanner()._stdlib_row("now") is not None
    assert _scanner(generic_funcs={"now": object()})._stdlib_row("now") is None
    assert _scanner(func_table={"copy": object()})._stdlib_row("copy") is None


def test_named_ok_reads_the_interned_spelling():
    """A nested generic payload is looked up by `Maybe<List<i32>>`, never `List@(i32)`."""
    from sushi_lang.backend.expressions.calls.utils import _named_ok
    from sushi_lang.semantics.generics.types import GenericTypeRef
    from sushi_lang.semantics.typesys import BuiltinType

    interned = object()
    codegen = SimpleNamespace(
        enum_table=SimpleNamespace(by_name={"Maybe<List<i32>>": interned}),
        struct_table=SimpleNamespace(by_name={}))
    payload = GenericTypeRef("Maybe", (GenericTypeRef("List", (BuiltinType.I32,)),))
    assert _named_ok(codegen, payload) is interned
