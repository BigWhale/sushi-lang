"""Coverage ratchet: error/warning .sushi tests that do not assert a diagnostic code."""

from pathlib import Path


# The maximum number of error/warning tests with no EXPECT_ERROR_CODE directive
# in their header (the "gap"). After each backfill pass this MUST be lowered to
# the new gap count -- it may never increase (that would mean a new error/warning
# test landed without a code assertion).
BASELINE = 0

TESTS_ROOT = Path(__file__).parent.parent  # tests/
EXCLUDED_DIRS = {"helpers", "bin"}
# The parser only inspects the first 20 lines; match that window here.
HEADER_LINES = 20


def _compute_gap() -> list[str]:
    """Return error/warning test files lacking an EXPECT_ERROR_CODE directive."""
    gap_files = []
    for f in sorted(TESTS_ROOT.rglob("test_*.sushi")):
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        if not (f.name.startswith("test_err_") or f.name.startswith("test_warn_")):
            continue
        header = "\n".join(f.read_text(encoding="utf-8").split("\n")[:HEADER_LINES])
        if "EXPECT_ERROR_CODE" not in header:
            gap_files.append(str(f.relative_to(TESTS_ROOT)))
    return gap_files


def test_diagnostic_coverage_ratchet():
    """Assert the diagnostic-code coverage gap does not exceed BASELINE."""
    gap_files = _compute_gap()
    gap = len(gap_files)
    assert gap <= BASELINE, (
        f"diagnostic coverage gap ({gap}) exceeds BASELINE ({BASELINE}).\n"
        f"Either:\n"
        f"  1. Add EXPECT_ERROR_CODE to the new error/warning test(s), OR\n"
        f"  2. Lower BASELINE to {gap} if you intentionally backfilled more tests.\n"
        f"\nFiles in gap ({gap}):\n" + "\n".join(f"  {f}" for f in gap_files[:30])
        + (f"\n  ... and {gap - 30} more" if gap > 30 else "")
    )
