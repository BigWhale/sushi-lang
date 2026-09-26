"""The stdlib builder is one table and one loop (#912).

A stdlib bitcode unit is one row of `STDLIB_BITCODE_UNITS`. The linker keeps no list
of the `.bc` files: it resolves `use <unit>` to `<unit>.bc`, a unit in `_virtual_units`
to none. So the rows are checked against that resolution and against the registry.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

from sushi_lang.backend.stdlib_linker import StdlibLinker
from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES, StdlibRegistry
from sushi_lang.sushi_stdlib import build
from sushi_lang.sushi_stdlib.build import STDLIB_BITCODE_UNITS

UNITS = [row.unit for row in STDLIB_BITCODE_UNITS]


def test_build_all_is_the_only_build_function():
    tree = ast.parse(Path(build.__file__).read_text())
    names = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("build_")
    ]
    assert names == ["build_all"]


def test_each_unit_has_one_row():
    assert len(UNITS) == len(set(UNITS))


def test_every_generator_row_names_a_generator():
    for row in STDLIB_BITCODE_UNITS:
        module = importlib.import_module(row.generator)
        assert callable(getattr(module, "generate_module_ir", None)), row.generator


def test_every_registry_module_has_a_row():
    assert set(StdlibRegistry.KNOWN_MODULES) <= set(UNITS)


def test_no_row_is_a_unit_that_resolves_to_no_bitcode():
    assert not set(UNITS) & StdlibLinker._virtual_units
    assert not set(UNITS) & set(SOURCE_STDLIB_MODULES)


def test_the_linker_resolves_each_unit_to_its_row_output(tmp_path):
    platform = "probe"
    for row in STDLIB_BITCODE_UNITS:
        target = tmp_path / platform / row.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    linker = StdlibLinker.__new__(StdlibLinker)
    linker.stdlib_dir = tmp_path
    linker.platform = platform
    for row in STDLIB_BITCODE_UNITS:
        assert linker._resolve_stdlib_unit(row.unit) == [tmp_path / platform / row.output]
