"""Sanity checks for the unit-test infrastructure itself."""
from __future__ import annotations

import os
import shutil

import pytest

from sushi_lang.compiler.fingerprint import compute_unit_fingerprint


CLEAN_PROGRAM = """
fn main() i32:
    return Result.Ok(0)
"""


def test_make_unit_builds_a_loaded_unit(make_unit):
    unit = make_unit(CLEAN_PROGRAM)
    assert unit.name == "main"
    assert unit.ast is not None
    assert unit.file_path.exists()


def test_make_unit_fixture_is_usable_by_fingerprint(make_unit):
    unit = make_unit(CLEAN_PROGRAM)
    fp = compute_unit_fingerprint(unit)
    assert isinstance(fp, str)
    assert len(fp) == 64  # SHA-256 hex digest


def test_analyze_clean_program_reports_no_errors(analyze):
    reporter = analyze(CLEAN_PROGRAM)
    assert not reporter.has_errors


# Nineteen skip sites across this layer are guarded on `shutil.which("sushic")`, each
# written as a skipif so a developer without the console script still gets a useful run.
# On CI that leniency is the wrong default: a broken install turns nineteen sites into
# silent skips and the suite still reports green. Here the same condition is fatal.
@pytest.mark.skipif(not os.environ.get("CI"), reason="local runs may lack the console script")
def test_sushic_is_on_path_in_ci():
    """CI must run the subprocess layer, not skip past it."""
    assert shutil.which("sushic") is not None, (
        "sushic is not on PATH, so every subprocess-backed test in tests/unit/ would skip "
        "rather than run. Install the project (`uv run pytest`) so the console script exists."
    )
