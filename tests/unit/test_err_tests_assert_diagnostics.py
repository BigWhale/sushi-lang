"""Guard: a test_err_ / test_warn_ test must assert WHICH diagnostic it expects."""

import re
import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).parent.parent  # tests/

if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import corpus_files, corpus_text  # noqa: E402
EXCLUDED_DIRS = {"helpers", "bin"}

_DIRECTIVE = re.compile(r"^\s*#\s*(EXPECT_ERROR_CODE|EXPECT_STDERR_CONTAINS)\s*:", re.M)


def _scan() -> list[str]:
    """Return test_err_/test_warn_ files that assert no specific diagnostic."""
    naked = []
    for f in corpus_files(TESTS_ROOT, "test_*.sushi"):
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        if not (f.name.startswith("test_err_") or f.name.startswith("test_warn_")):
            continue
        if not _DIRECTIVE.search(corpus_text(f)):
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
