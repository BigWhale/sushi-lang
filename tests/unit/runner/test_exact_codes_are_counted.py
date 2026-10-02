"""`EXPECT_ERROR_CODES_EXACT` compares a MULTISET of codes, through the real runner (#1060).

`CE0112` means exactly one CE0112, and `CE0112, CE0112` means two. A set compare passed a
fixture that states a code once when the compiler prints it twice, so a duplicate
diagnostic was invisible to every fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import run_single, detail

# Two CE0112 at two positions, and nothing else.
TWICE = ("fn main() i32:\n    let i32 a = 1 / 0\n    let i32 b = 2 / 0\n"
         "    println(a + b)\n    return 0\n")
# Two CW1001 and nothing else.
WARNS_TWICE = "fn main() i32:\n    let i32 x = 1\n    let i32 y = 2\n    return 0\n"


def _run(tmp_path: Path, name: str, header: str, body: str):
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return run_single(path)


@pytest.mark.parametrize("name, header, body, passes", [
    ("test_err_count.sushi", "# EXPECT_ERROR_CODES_EXACT: CE0112\n", TWICE, False),
    ("test_err_count.sushi", "# EXPECT_ERROR_CODES_EXACT: CE0112, CE0112\n", TWICE, True),
    ("test_err_count.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE0112\n# EXPECT_ERROR_CODES_EXACT: CE0112\n", TWICE, True),
    ("test_err_count.sushi",
     "# EXPECT_ERROR_CODES_EXACT: CE0112, CE0112, CE0112\n", TWICE, False),
    ("test_warn_count.sushi", "# EXPECT_ERROR_CODES_EXACT: CW1001\n", WARNS_TWICE, False),
    ("test_warn_count.sushi", "# EXPECT_ERROR_CODES_EXACT: CW1001, CW1001\n", WARNS_TWICE, True),
], ids=["one too few", "the exact count", "the count over two directives",
        "one too many", "a warning, one too few", "a warning, the exact count"])
def test_the_count_decides_the_verdict(tmp_path, name, header, body, passes):
    result = _run(tmp_path, name, header, body)
    assert result.total_success is passes, detail(result)


def test_the_failure_names_the_code_and_both_counts(tmp_path):
    result = _run(tmp_path, "test_err_count.sushi", "# EXPECT_ERROR_CODES_EXACT: CE0112\n", TWICE)
    assert not result.total_success
    assert "CE0112: expected 1, printed 2" in result.compilation_message, detail(result)
