"""
Coverage ratchet: error/warning .sushi tests that do not assert a diagnostic code.

A `test_err_*` / `test_warn_*` test only proves the compiler *failed* (exit 2/1).
It does not prove *which* diagnostic fired. The `EXPECT_ERROR_CODE` directive
(enforced on the compilation path by enhanced_test_runner) pins the emitted code,
so a diagnostic that regresses to the wrong code turns the suite red.

This ratchet tracks the gap (error/warning tests with no EXPECT_ERROR_CODE) and
only allows it to shrink, mirroring test_stdout_coverage.py.

BASELINE should be LOWERED as more directories are backfilled. Backfilled so far:
- tests/diagnostics/, tests/types/, tests/generics/ (100%);
- tests/constants/, tests/control_flow/, tests/operators/, tests/literals/,
  tests/io/, tests/libs/ (Batch A);
- tests/strings/, tests/enums/, tests/list/, tests/error_handling/, tests/memory/,
  tests/perks/ (Batch B).
Known-bug / limitation exceptions left in the gap (no honest code to assert):
- tests/control_flow/test_err_break_continue_outside_loop -- break/continue outside
  a loop hits a backend assert instead of emitting CE1003 (issue: CE1003).
- tests/list/test_err_list_type_mismatch -- List.push() wrong-type escapes the type
  checker into LLVM codegen (no code).
- tests/error_handling/test_err_let_result_constructor -- emits internal-bug CE0113
  instead of CE2505.
- tests/perks/test_err_perk_no_methods -- parse error (Lark), not a CE#### diagnostic.
Not yet backfilled (deliberate follow-up passes):
- tests/array/, tests/basic/, tests/stdlib/

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
BASELINE = 86

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
