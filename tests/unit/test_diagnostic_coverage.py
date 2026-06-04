"""
Coverage ratchet: error/warning .sushi tests that do not assert a diagnostic code.

A `test_err_*` / `test_warn_*` test only proves the compiler *failed* (exit 2/1).
It does not prove *which* diagnostic fired. The `EXPECT_ERROR_CODE` directive
(enforced on the compilation path by enhanced_test_runner) pins the emitted code,
so a diagnostic that regresses to the wrong code turns the suite red.

This ratchet tracks the gap (error/warning tests with no EXPECT_ERROR_CODE) and
only allows it to shrink, mirroring test_stdout_coverage.py.

All error/warning directories are now backfilled (Batches A/B/C). The three
semantic-validation-gap bugs (#46/#47/#48) have been fixed and their tests now
assert codes (CE1003 / CE2006 / CE2505), so the remaining gap is exactly the 2
parse-error tests below, each of which has no honest CE#### to assert.
BASELINE may only go DOWN -- lower it when one of these is resolved.

Parse-error limitations (Lark raises before semantic analysis; no CE#### code):
- tests/perks/test_err_perk_no_methods -- empty perk body.
- tests/array/test_err_hash_nested_arrays -- nested array type syntax i32[2][2].

To lower BASELINE after a backfill pass:
1. Run this file (it prints the current gap on failure), or recompute manually:
       python3 -c "
       from pathlib import Path
       excluded = {'helpers', 'bin'}
       gap = 0
       for f in sorted(Path('tests').rglob('test_*.sushi')):
           if any(x in excluded for x in f.parts):
               continue
           if not (f.name.startswith('test_err_') or f.name.startswith('test_warn_')):
               continue
           if 'EXPECT_ERROR_CODE' not in '\n'.join(f.read_text().split('\n')[:20]):
               gap += 1
       print(gap)
       "
2. Update BASELINE to the new gap count.
3. Commit.
"""

from pathlib import Path


# The maximum number of error/warning tests with no EXPECT_ERROR_CODE directive
# in their header (the "gap"). After each backfill pass this MUST be lowered to
# the new gap count -- it may never increase (that would mean a new error/warning
# test landed without a code assertion).
BASELINE = 2

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
    """
    Assert the diagnostic-code coverage gap does not exceed BASELINE.

    A gap = an error/warning test (test_err_* / test_warn_*) with no
    EXPECT_ERROR_CODE directive in its first 20 lines.

    If this fails because you added a new error/warning test, add an
    EXPECT_ERROR_CODE directive to it. If you backfilled more tests, lower
    BASELINE to the printed gap count.
    """
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
