"""
Coverage ratchet: success-category .sushi tests that print but lack stdout assertions.

The R0 test-correctness baseline (local ROADMAP.md Phase R0.2) drove this gap to 0: every
success-category test that calls print/println either asserts its stdout (EXPECT_STDOUT_EXACT /
EXPECT_STDOUT_CONTAINS) or is recorded in QUARANTINE below. BASELINE is therefore 0 — the ratchet
now only ever admits a new printing test that asserts its output.

Two exclusion paths, and only two:
  1. The test asserts its stdout (has an EXPECT_STDOUT_* directive).
  2. The test is listed in QUARANTINE (a known compiler bug it repros, an intentionally silent
     test, or output that can't be asserted deterministically). Each such .sushi file is left
     UNCHANGED; QUARANTINE is the single external record. See test_quarantine_registry_valid.

To add a new printing test: give it an EXPECT_STDOUT_* assertion (do not raise BASELINE).
When a quarantined bug is fixed: add the assertion to that test and delete its QUARANTINE entry.

How to compute the gap manually (from the repo root) — must print 0:
    python3 -c "
    import re
    from pathlib import Path
    import sys; sys.path.insert(0, 'tests/unit')
    from test_stdout_coverage import QUARANTINE
    excluded = {'helpers', 'bin'}
    gap = 0
    for f in sorted(Path('tests').rglob('test_*.sushi')):
        rel = str(f.relative_to('tests'))
        if any(x in excluded for x in f.parts):
            continue
        if f.name.startswith('test_err_') or f.name.startswith('test_warn_'):
            continue
        if rel in QUARANTINE:
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
#
# The R0 test-correctness baseline (see local ROADMAP.md Phase R0.2) drives this
# to 0: every printing success-category test either asserts its stdout, or — if it
# reveals a real compiler bug (wrong output), is intentionally silent, or cannot be
# asserted deterministically — is recorded in QUARANTINE below and excluded from the
# gap. Once at 0 the ratchet only ever admits new printing tests that assert output.
BASELINE = 0

TESTS_ROOT = Path(__file__).parent.parent  # tests/
EXCLUDED_DIRS = {"helpers", "bin"}

# --------------------------------------------------------------------------- #
# Quarantine registry (R0.2)
#
# Printing tests that are intentionally left WITHOUT a stdout assertion, keyed by
# path relative to tests/. The .sushi files themselves are never edited — this is
# the single external record of why each is exempt. _compute_gap() skips them so
# the gap can reach 0 while the underlying issues stay tracked.
#
# Each entry: {"reason": <reason>, "issue": <url or None>}
#   "broken-output" — the compiler emits WRONG output; the test is a real bug repro.
#                     Requires a tracking issue. Fixing the bug means adding the
#                     EXPECT_STDOUT_* assertion and removing the entry here.
#   "needs-triage"  — the correct output could not be confidently derived; do NOT
#                     lock in possibly-wrong output. Requires a tracking issue.
#   "no-stdout"     — the test matches the print/println regex (e.g. in a comment or
#                     string) but emits no assertable runtime stdout. A coverage
#                     exclusion, not a bug — no issue required.
#
# The test_quarantine_registry_valid guard below keeps this honest: every entry must
# point at a real file that still lacks a directive, and every non-"no-stdout" entry
# must carry an issue URL.
QUARANTINE: dict[str, dict] = {
    # --- broken-output: the compiler emits wrong output / crashes (real bug repros) ---
    # --- no-stdout: matches the print/println scan but emits no assertable runtime output ---
    # (error path taken before print, uncalled printing helper, or empty-collection iteration)
    "array/test_dynamic_arrays_bounds_runtime.sushi": {"reason": "no-stdout", "issue": None},
    "basic/test_function_calls.sushi": {"reason": "no-stdout", "issue": None},
    "list/test_list_match_get.sushi": {"reason": "no-stdout", "issue": None},
    "list/test_tiny.sushi": {"reason": "no-stdout", "issue": None},
    "strings/test_interpolation_example.sushi": {"reason": "no-stdout", "issue": None},
    "stdlib/generics/hashmap/test_hashmap_entries_empty.sushi": {"reason": "no-stdout", "issue": None},
    "stdlib/generics/hashmap/test_hashmap_keys_empty.sushi": {"reason": "no-stdout", "issue": None},
}

_VALID_QUARANTINE_REASONS = {"broken-output", "needs-triage", "no-stdout"}


def _compute_gap() -> list[str]:
    """Return list of files in the gap (print but no stdout assertion)."""
    gap_files = []
    for f in sorted(TESTS_ROOT.rglob("test_*.sushi")):
        rel = str(f.relative_to(TESTS_ROOT))
        # Skip non-test dirs
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        # Skip error/warning categories
        if f.name.startswith("test_err_") or f.name.startswith("test_warn_"):
            continue
        # Skip quarantined tests (tracked separately; see QUARANTINE + guard test)
        if rel in QUARANTINE:
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


def test_quarantine_registry_valid():
    """Keep the QUARANTINE registry honest.

    Every entry must:
      - point at a file that still exists (a moved/deleted test must be de-listed),
      - carry a recognized reason,
      - still lack an EXPECT_STDOUT_* directive (once a test is fixed and asserted,
        it must be removed from QUARANTINE — otherwise it is silently double-counted
        as both asserted and exempt), and
      - carry a tracking issue URL unless the reason is "no-stdout".
    """
    problems = []
    for rel, meta in QUARANTINE.items():
        path = TESTS_ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}: quarantined path does not exist")
            continue
        reason = meta.get("reason")
        if reason not in _VALID_QUARANTINE_REASONS:
            problems.append(
                f"{rel}: invalid reason {reason!r} (expected one of {sorted(_VALID_QUARANTINE_REASONS)})"
            )
        content = path.read_text(encoding="utf-8")
        if "EXPECT_STDOUT_CONTAINS" in content or "EXPECT_STDOUT_EXACT" in content:
            problems.append(
                f"{rel}: has an EXPECT_STDOUT_* directive — remove it from QUARANTINE"
            )
        if reason != "no-stdout" and not meta.get("issue"):
            problems.append(f"{rel}: reason {reason!r} requires a tracking issue URL")

    assert not problems, "quarantine registry invalid:\n" + "\n".join(f"  {p}" for p in problems)
