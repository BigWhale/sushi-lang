"""A struct collected after an enum of one name is refused, like the mirror case (#901).

A type name is one per program. The second declaration, in collection order and of
either kind, is CE0006 at the declaration and is the one fault. The code LIST is
asserted, because `EXPECT_ERROR_CODE` is a substring test and cannot prove a code is
absent: on the analyze path for two ordinary units, and on the rendered output for a
source library.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sushi_lang.compiler.loader import load_unit_recursively
from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.semantic_analyzer import SemanticAnalyzer
from sushi_lang.semantics.stdlib_registry import get_stdlib_registry
from sushi_lang.semantics.units import UnitManager
from sushic_path import SUSHIC, needs_sushic

_TESTS = Path(__file__).resolve().parents[1]
_UNITS = _TESTS / "units" / "struct_after_enum"

_CASES = {
    "test_err_struct_after_enum.sushi": ["CE0006"],
    "test_err_struct_after_generic_enum.sushi": ["CE0006"],
    "test_err_generic_struct_after_enum.sushi": ["CE0006"],
}


def _analyze_units(tmp_path: Path, fixture: str) -> Reporter:
    """Load the fixture and every unit it imports, analyze, and return the reporter."""
    shutil.copytree(_UNITS / "helpers", tmp_path / "helpers")
    source = (_UNITS / fixture).read_text(encoding="utf-8")
    (tmp_path / "main.sushi").write_text(source, encoding="utf-8")
    reporter = Reporter(source=source, filename="main")
    get_stdlib_registry()

    unit_manager = UnitManager(root_path=tmp_path, reporter=reporter)
    program, _ = parse_to_ast(source)
    unit = unit_manager.load_unit("main", program)
    assert unit is not None
    loaded = {"main"}
    for dep in unit.dependencies:
        assert load_unit_recursively(unit_manager, dep, loaded, reporter)
    assert unit_manager.get_compilation_order() is not None

    analyzer = SemanticAnalyzer(reporter, filename="main", unit_manager=unit_manager)
    try:
        analyzer.check()
    except ValueError:
        pass
    return reporter


def _codes(reporter: Reporter) -> list[str]:
    return [item.code for item in reporter.items if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_a_struct_after_an_enum_is_one_diagnostic(tmp_path, fixture):
    """The struct that comes second hears CE0006 alone."""
    assert _codes(_analyze_units(tmp_path, fixture)) == _CASES[fixture]


def test_the_diagnostic_names_both_kinds_and_points_at_the_enum(tmp_path):
    """The head line says a struct met an enum; the note points at the enum."""
    reporter = _analyze_units(tmp_path, "test_err_struct_after_enum.sushi")
    [item] = [i for i in reporter.items if i.code == "CE0006"]
    assert item.message == "struct 'Acc' already defined as enum"
    [note] = item.sub
    assert note.span is not None
    assert note.filename is not None and note.filename.endswith("acc_enum.sushi")


@needs_sushic
def test_a_source_library_enum_against_a_consumer_struct(tmp_path):
    """A public library enum against a consumer struct of one name: CE0006 alone."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir()
    built = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", "source", "--lib-version", "1.0.0",
         str(_TESTS / "libs" / "helpers" / "types_lib.sushi"),
         "-o", str(libs_dir / "types_lib.slib")],
        cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr

    project = tmp_path / "prog"
    project.mkdir()
    fixture = _TESTS / "libs" / "struct_against_lib_enum" / \
        "test_err_lib_source_struct_against_enum.sushi"
    (project / "main.sushi").write_text(fixture.read_text(), encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out"], cwd=project, capture_output=True,
        text=True, env={**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"})
    out = result.stdout + result.stderr

    assert result.returncode == 2, out
    assert re.findall(r"\b(C[EW]\d{4})\b", out) == ["CE0006"], out
