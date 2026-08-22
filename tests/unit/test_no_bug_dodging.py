"""Guard: a test may not quietly avoid exercising something because of a bug.

Two corpora, two evasion channels. A `.sushi` test has no skip mechanism, so it dodges in
prose -- a comment saying the assertion is absent because something is broken. A Python test
dodges through the machinery instead: a `pytest.skip` reason, or an `xfail` that turns a red
test green. Each half of this file scans the channel its corpus actually uses.
"""

import ast
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


def _skip_reasons(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Return (lineno, marker, reason) for every skip/xfail reason string in one module.

    Only the reason STRINGS are read, never surrounding prose. Scanning Python comments the
    way the .sushi half does produces false positives the moment a test legitimately talks
    about a compiler bug -- asserting a diagnostic contains the phrase, say, or documenting
    a quarantine taxonomy. The reason string is where an actual Python dodge has to live.
    """
    found = []
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called.add(id(func))
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"skip", "skipif", "xfail", "importorskip"}:
            continue
        strings = [a.value for a in node.args if isinstance(a, ast.Constant)
                   and isinstance(a.value, str)]
        strings += [k.value.value for k in node.keywords
                    if k.arg == "reason" and isinstance(k.value, ast.Constant)
                    and isinstance(k.value.value, str)]
        # A reasonless `xfail(...)` still has to be reported, or the commonest form of the
        # marker is the one that gets through.
        for text in strings or [""]:
            found.append((node.lineno, name, text))

    # Bare `@pytest.mark.xfail`, with no call parentheses, is an Attribute rather than a
    # Call -- the form most likely to be reached for, and the one an all-Calls walk misses.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "xfail" and id(node) not in called:
            found.append((node.lineno, "xfail", ""))
    return found


def _scan_python() -> list[tuple[str, int, str]]:
    """Return (relpath, lineno, text) for each Python dodge outside the registry."""
    hits = []
    for f in sorted(TESTS_ROOT.rglob("*.py")):
        parts = f.relative_to(TESTS_ROOT).parts
        if any(d in EXCLUDED_DIRS or d == "__pycache__" for d in parts):
            continue
        rel = str(f.relative_to(TESTS_ROOT))
        if rel in BUG_DODGE_REGISTRY:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for lineno, marker, reason in _skip_reasons(tree):
            # An xfail is a dodge by construction: it takes a failing test and reports it
            # green. The suite has none today, and this keeps it that way.
            if marker == "xfail" or _DODGE.search(reason):
                hits.append((rel, lineno, f"{marker}: {reason}"))
    return hits


def test_no_undocumented_bug_dodging():
    """A test may not cite a bug as its reason for not asserting something."""
    hits = _scan() + _scan_python()
    assert not hits, (
        "Test(s) cite a compiler bug, or mark an expected failure, but are not in\n"
        "BUG_DODGE_REGISTRY. Either fix the bug and restore the assertion, or register\n"
        "the dodge with a tracking issue in tests/unit/test_no_bug_dodging.py.\n\n"
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
