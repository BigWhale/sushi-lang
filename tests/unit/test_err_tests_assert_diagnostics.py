"""
Guard: a test_err_ / test_warn_ test must assert WHICH diagnostic it expects.

The compilation phase only checks the exit code implied by the filename: 2 for
test_err_, 1 for test_warn_. An exit of 2 says some error happened, not the error the
test was written for. Four tests relied on that, and one of them had never tested the
thing it named: test_err_hash_nested_arrays declared `i32[2][2]`, which the parser
rejects, so the compiler never reached the hash check it existed to exercise -- and
CE0052 was asserted nowhere in the tree.

Most diagnostics carry a CE/CW code and should use EXPECT_ERROR_CODE. A raw parse
error carries no code, so those tests assert the message text with
EXPECT_STDERR_CONTAINS instead. Either is enough; neither is optional.
"""

import re
from pathlib import Path

TESTS_ROOT = Path(__file__).parent.parent  # tests/
EXCLUDED_DIRS = {"helpers", "bin"}

_DIRECTIVE = re.compile(r"^\s*#\s*(EXPECT_ERROR_CODE|EXPECT_STDERR_CONTAINS)\s*:", re.M)


def _scan() -> list[str]:
    """Return test_err_/test_warn_ files that assert no specific diagnostic."""
    naked = []
    for f in sorted(TESTS_ROOT.rglob("test_*.sushi")):
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        if not (f.name.startswith("test_err_") or f.name.startswith("test_warn_")):
            continue
        if not _DIRECTIVE.search(f.read_text(encoding="utf-8")):
            naked.append(str(f.relative_to(TESTS_ROOT)))
    return naked


def test_err_and_warn_tests_assert_a_diagnostic():
    naked = _scan()
    assert not naked, (
        "test_err_/test_warn_ test(s) assert no specific diagnostic, so they pass on\n"
        "ANY compile failure -- including an unrelated syntax error in the test itself.\n"
        "Add EXPECT_ERROR_CODE: CExxxx, or EXPECT_STDERR_CONTAINS for an uncoded parse\n"
        "error.\n\n" + "\n".join(f"  {f}" for f in naked)
    )
