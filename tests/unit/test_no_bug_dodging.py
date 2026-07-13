"""
Guard: a .sushi test may not quietly avoid exercising something because of a bug.

Tier 0 found five tests that narrowed their own coverage in a `#` comment -- "u32
comparisons only (arithmetic ops have compiler bug)", "Skip this test for now" above a
commented-out `const u32 NOT_MASK = ~(0x00 as u32)" -- for bugs that either no longer
existed or had never been filed. Nothing checked those comments, so the lost coverage
was invisible. One of them was hiding a live formatter defect.

The project already has the right shape for this: the QUARANTINE registry in
test_stdout_coverage.py demands a reason AND a tracking issue, and a guard keeps it
honest. This is the same idea for the .sushi files themselves.

To dodge a bug in a test, you must say so in the registry below, with an issue. To fix
one, delete its entry and restore the assertion.
"""

import re
from pathlib import Path

TESTS_ROOT = Path(__file__).parent.parent  # tests/
EXCLUDED_DIRS = {"helpers", "bin"}

# Phrases that mean "this test is not exercising something, because of a defect".
# Deliberately narrow: prose describing an ALREADY-FIXED bug (a regression test's
# rationale) is normal and must not trip this. Only present-tense evasion is caught.
DODGE_PATTERNS = [
    r"\bKNOWN BUG\b",
    r"\bcompiler bug\b",
    r"\bskip (?:this )?test\b",
    r"\bskipped due to\b",
]
_DODGE = re.compile("|".join(DODGE_PATTERNS), re.I)

# Tests that legitimately dodge a bug, keyed by path relative to tests/.
# Each entry needs a reason and a tracking issue URL. Empty is the goal state.
#   {"reason": "...", "issue": "https://github.com/BigWhale/sushi-lang/issues/N"}
BUG_DODGE_REGISTRY: dict[str, dict] = {}


def _scan() -> list[tuple[str, int, str]]:
    """Return (relpath, lineno, text) for each dodging comment outside the registry."""
    hits = []
    for f in sorted(TESTS_ROOT.rglob("test_*.sushi")):
        rel = str(f.relative_to(TESTS_ROOT))
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        if rel in BUG_DODGE_REGISTRY:
            continue
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") and _DODGE.search(stripped):
                hits.append((rel, lineno, stripped))
    return hits


def test_no_undocumented_bug_dodging():
    """A test may not cite a bug as its reason for not asserting something."""
    hits = _scan()
    assert not hits, (
        "Test(s) cite a compiler bug in a comment but are not in BUG_DODGE_REGISTRY.\n"
        "Either fix the bug and restore the assertion, or register the dodge with a\n"
        "tracking issue in tests/unit/test_no_bug_dodging.py.\n\n"
        + "\n".join(f"  {f}:{n}\n      {t}" for f, n, t in hits)
    )


def test_bug_dodge_registry_valid():
    """Every registry entry must point at a real file and carry an issue URL."""
    problems = []
    for rel, meta in BUG_DODGE_REGISTRY.items():
        if not (TESTS_ROOT / rel).is_file():
            problems.append(f"{rel}: registered path does not exist")
        if not meta.get("reason"):
            problems.append(f"{rel}: missing reason")
        issue = meta.get("issue", "")
        if not issue.startswith("http"):
            problems.append(f"{rel}: reason requires a tracking issue URL")
    assert not problems, "bug-dodge registry invalid:\n" + "\n".join(f"  {p}" for p in problems)
