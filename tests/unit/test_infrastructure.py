"""Sanity checks for the unit-test infrastructure itself.

Verifies that the shared fixtures (make_unit, analyze) and the core compiler
entrypoints they wrap are wired correctly, so the Phase B suites can rely on
them. These are intentionally minimal.
"""
from __future__ import annotations

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
