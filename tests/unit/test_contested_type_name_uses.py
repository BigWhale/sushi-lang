"""A type name that lost a struct/enum clash is one diagnostic, at its uses too (#863).

A TYPE is one name for the whole program. The unit that declares it second hears
CE0004 (struct against struct) or CE0006 (enum against struct) at its declaration, and
that is the one fault. Its later uses of the name must not be judged against the
winner's type: CE2105 at a variant construction, CE2048 at a `match`, CE2102 at a
payload variant, and the field checks of a construction all read a type the user did
not write.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is ABSENT, so the code
LIST is asserted here: on the analyze path for two ordinary units, and on the rendered
output for a source library.
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
_UNITS = _TESTS / "units" / "contested_type"

_CASES = {
    "test_err_contested_enum_uses.sushi": ["CE0006"],
    "test_err_contested_struct_uses.sushi": ["CE0004"],
    "test_contested_control_enum.sushi": [],
}


def _analyze_units(tmp_path: Path, fixture: str) -> list[str]:
    """Load the fixture and every unit it imports, analyze, and return the CE codes."""
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
    return [item.code for item in reporter.items if item.code.startswith("CE")]


@pytest.mark.parametrize("fixture", list(_CASES))
def test_a_contested_name_is_one_diagnostic(tmp_path, fixture):
    """The loser hears its declaration's code alone; the control hears nothing."""
    assert _analyze_units(tmp_path, fixture) == _CASES[fixture]


@needs_sushic
def test_a_source_library_struct_against_a_consumer_enum(tmp_path):
    """A public library struct against a consumer enum of one name: CE0006 alone."""
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
    fixture = _TESTS / "libs" / "contested_member" / \
        "test_err_lib_source_contested_enum_uses.sushi"
    (project / "main.sushi").write_text(fixture.read_text(), encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out"], cwd=project, capture_output=True,
        text=True, env={**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"})
    out = result.stdout + result.stderr

    assert result.returncode == 2, out
    assert re.findall(r"\b(C[EW]\d{4})\b", out) == ["CE0006"], out
