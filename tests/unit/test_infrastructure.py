"""Sanity checks for the unit-test infrastructure itself."""
from __future__ import annotations

from pathlib import Path

from sushi_lang.compiler.fingerprint import compute_unit_fingerprint
from sushic_path import SUSHIC, SUSHIC_AVAILABLE


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


# Every skip site across this layer is guarded on `needs_sushic`, which reads the
# driver of THIS checkout. A guard that answers False turns the whole subprocess
# layer into silent skips while the suite still reports green, so the same
# condition is asserted here.
def test_the_compiler_under_test_is_this_checkout():
    """The subprocess layer runs the tree it sits in, not whichever is on PATH (#530)."""
    assert SUSHIC_AVAILABLE, f"no compiler driver at {SUSHIC}"
    assert Path(SUSHIC).parent == Path(__file__).resolve().parents[2]
