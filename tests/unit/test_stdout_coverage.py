"""
Coverage ratchet: success-category .sushi tests that print but lack stdout assertions.

BASELINE should be lowered as more directories are backfilled. Current coverage status:
- BACKFILLED (assertions added in p0-3-runtime-validation):
    tests/operators/, tests/literals/, tests/constants/, tests/control_flow/,
    tests/array/, tests/list/, tests/strings/, tests/basic/
- NOT YET BACKFILLED (deliberate follow-up pass):
    tests/stdlib/, tests/types/, tests/memory/, tests/generics/,
    tests/perks/, tests/io/, tests/error_handling/

To lower BASELINE after a follow-up backfill pass:
1. Run: python3 -c "import re; from pathlib import Path; ..."  (see script below)
2. Update BASELINE to the new gap count
3. Commit

How to compute the gap manually (from the repo root):
    python3 -c "
    import re
    from pathlib import Path
    excluded = {'helpers', 'bin'}
    gap = 0
    for f in sorted(Path('tests').rglob('test_*.sushi')):
        if any(x in excluded for x in f.parts):
            continue
        if f.name.startswith('test_err_') or f.name.startswith('test_warn_'):
            continue
        c = f.read_text()
        if re.search(r'\bprint\b|\bprintln\b', c) and \
           'EXPECT_STDOUT_CONTAINS' not in c and 'EXPECT_STDOUT_EXACT' not in c:
            gap += 1
    print(gap)
    "
"""

import re
from pathlib import Path


# The maximum number of success-category tests that call print/println but have
# no EXPECT_STDOUT_CONTAINS or EXPECT_STDOUT_EXACT in their header (the "gap").
# After each backfill pass this MUST be lowered to the new gap count — it may
# never increase (that would mean new unasserted printing tests were added).
BASELINE = 257

TESTS_ROOT = Path(__file__).parent.parent  # tests/
EXCLUDED_DIRS = {"helpers", "bin"}


def _compute_gap() -> list[str]:
    """Return list of files in the gap (print but no stdout assertion)."""
    gap_files = []
    for f in sorted(TESTS_ROOT.rglob("test_*.sushi")):
        # Skip non-test dirs
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        # Skip error/warning categories
        if f.name.startswith("test_err_") or f.name.startswith("test_warn_"):
            continue
        content = f.read_text(encoding="utf-8")
        has_print = bool(re.search(r"\bprint\b|\bprintln\b", content))
        has_assert = (
            "EXPECT_STDOUT_CONTAINS" in content
            or "EXPECT_STDOUT_EXACT" in content
        )
        if has_print and not has_assert:
            gap_files.append(str(f.relative_to(TESTS_ROOT)))
    return gap_files


def test_stdout_coverage_ratchet():
    """
    Assert the coverage gap does not exceed BASELINE.

    A gap = a success-category test that calls print/println but has no
    EXPECT_STDOUT_CONTAINS or EXPECT_STDOUT_EXACT in its first 20 lines (the
    parser only inspects the first 20 lines, but the simplest check is the
    whole file since directives are always at the top).

    If this test fails because you added a new test that prints but has no
    assertion, add the assertion. If you backfilled more tests, lower BASELINE.
    """
    gap_files = _compute_gap()
    gap = len(gap_files)
    assert gap <= BASELINE, (
        f"stdout coverage gap ({gap}) exceeds BASELINE ({BASELINE}).\n"
        f"Either:\n"
        f"  1. Add EXPECT_STDOUT_CONTAINS to the new printing test(s), OR\n"
        f"  2. Lower BASELINE to {gap} if you intentionally backfilled more tests.\n"
        f"\nFiles in gap ({gap}):\n" + "\n".join(f"  {f}" for f in gap_files[:30])
        + (f"\n  ... and {gap - 30} more" if gap > 30 else "")
    )
