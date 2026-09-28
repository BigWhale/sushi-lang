"""Every `test_err_` and `test_warn_` fixture names the code it expects.

A fixture that names no code passes on ANY compile failure, including an unrelated
syntax error in the fixture itself. `EXPECT_STDERR_CONTAINS` alone is not enough: a
text match does not say which diagnostic printed it.
"""

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).parent.parent  # tests/

if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import corpus_files, corpus_text  # noqa: E402
EXCLUDED_DIRS = {"helpers", "bin"}
# The parser only inspects the first 20 lines; match that window here.
HEADER_LINES = 20


def _fixtures_with_no_code() -> list[str]:
    """Return the error and warning fixtures that hold no EXPECT_ERROR_CODE directive."""
    missing = []
    for f in corpus_files(TESTS_ROOT, "test_*.sushi"):
        if any(d in EXCLUDED_DIRS for d in f.relative_to(TESTS_ROOT).parts):
            continue
        if not (f.name.startswith("test_err_") or f.name.startswith("test_warn_")):
            continue
        header = "\n".join(corpus_text(f).split("\n")[:HEADER_LINES])
        if "EXPECT_ERROR_CODE" not in header:
            missing.append(str(f.relative_to(TESTS_ROOT)))
    return missing


def test_every_err_and_warn_fixture_names_its_code():
    missing = _fixtures_with_no_code()
    assert not missing, (
        f"{len(missing)} error or warning fixture(s) name no code. Add "
        "EXPECT_ERROR_CODE: CExxxx (or EXPECT_ERROR_CODES_EXACT) to each:\n"
        + "\n".join(f"  {f}" for f in missing[:30])
        + (f"\n  ... and {len(missing) - 30} more" if len(missing) > 30 else "")
    )
