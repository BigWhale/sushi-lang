"""A `test_warn_` fixture runs its binary when it states a runtime directive (#1253).

The runner ran the binary of a warning fixture only for `EXPECT_NO_LEAKS` or
`EXPECT_NO_OPEN_FDS`, so an `EXPECT_STDOUT_EXACT` alone was never checked and a wrong one
passed. And `EXPECT_STDERR_CONTAINS` on a warning fixture names the text of the COMPILER's
diagnostic; the runtime check read it again against the program's stderr, so a warning
fixture could not assert its warning and also run its binary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import run_single, detail

# One CW1002 at line 4 of the body; the program prints `2`.
SHADOWS = ("fn main() i32:\n    let i32 t = 1\n    if (t == 1):\n        let i32 t = 2\n"
           "        println(t)\n    return 0\n")


def _run(tmp_path: Path, header: str):
    path = tmp_path / "test_warn_shadow.sushi"
    path.write_text(header + SHADOWS, encoding="utf-8")
    return run_single(path)


@pytest.mark.parametrize("header", [
    '# EXPECT_STDOUT_EXACT: "this text is never printed\\n"\n',
    '# EXPECT_STDOUT_CONTAINS: "never printed"\n',
    "# EXPECT_RUNTIME_EXIT: 3\n",
], ids=["stdout exact", "stdout contains", "runtime exit"])
def test_a_wrong_runtime_directive_fails(tmp_path, header):
    result = _run(tmp_path, "# EXPECT_ERROR_CODES_EXACT: CW1002\n" + header)
    assert not result.skipped_runtime, detail(result)
    assert result.total_success is False, detail(result)


def test_a_right_runtime_directive_passes(tmp_path):
    result = _run(tmp_path, '# EXPECT_ERROR_CODES_EXACT: CW1002\n# EXPECT_STDOUT_EXACT: "2\\n"\n')
    assert not result.skipped_runtime, detail(result)
    assert result.total_success is True, detail(result)


@pytest.mark.parametrize("runtime", [
    '# EXPECT_STDOUT_EXACT: "2\\n"\n',
    '# EXPECT_STDOUT_EXACT: "2\\n"\n# EXPECT_NO_LEAKS: true\n',
], ids=["with stdout", "with a leak check"])
def test_the_warning_text_is_read_from_the_compiler_alone(tmp_path, runtime):
    result = _run(tmp_path, "# EXPECT_ERROR_CODES_EXACT: CW1002\n"
                            '# EXPECT_STDERR_CONTAINS: "test_warn_shadow.sushi:"\n' + runtime)
    assert not result.skipped_runtime, detail(result)
    assert result.total_success is True, detail(result)


def test_a_missing_warning_text_still_fails(tmp_path):
    result = _run(tmp_path, "# EXPECT_ERROR_CODES_EXACT: CW1002\n"
                            '# EXPECT_STDERR_CONTAINS: "no compiler prints this"\n'
                            '# EXPECT_STDOUT_EXACT: "2\\n"\n')
    assert result.total_success is False, detail(result)


def test_a_warning_fixture_with_no_runtime_directive_runs_nothing(tmp_path):
    result = _run(tmp_path, "# EXPECT_ERROR_CODES_EXACT: CW1002\n")
    assert result.skipped_runtime, detail(result)
    assert result.total_success is True, detail(result)
