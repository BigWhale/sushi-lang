"""A header line that looks like a directive is a directive the parser knows (#1041).

A line of the leading comment block that starts with an upper-case name of four or more
characters is read as a directive. An unknown name, a valued name with no `:`, and a
flag name followed by text that is not `: value` fail the fixture with a message that
names the line; the runner never drops a line that a reader takes as an assertion. A
line that starts with a diagnostic code (`CE2510: ...`) is prose.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[2]

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import parse_test_metadata  # noqa: E402

BODY = 'fn main() i32:\n    println("Mostly Harmless")\n    return Result.Ok(0)\n'
STDOUT = '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'


def _fixture(tmp_path: Path, header: str) -> Path:
    home = tmp_path / "fixture"
    home.mkdir(exist_ok=True)
    path = home / "test_directive_probe.sushi"
    path.write_text(STDOUT + header + BODY)
    return path


def _run(path: Path):
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


REFUSED = [
    ("# EXPECTED_OUTPUT: Mostly Harmless\n", "EXPECTED_OUTPUT"),
    ("# EXPECT_STDERR_EMPTY\n", "EXPECT_STDERR_EMPTY"),
    ("# EXPECT_RUNTIME_EXIT 0\n", "EXPECT_RUNTIME_EXIT"),
    ("# EXPECT_NO_LEAKS true\n", "EXPECT_NO_LEAKS"),
]


@pytest.mark.parametrize("header,name", REFUSED)
def test_a_line_the_parser_cannot_read_is_a_directive_error(tmp_path, header, name):
    errors = parse_test_metadata(_fixture(tmp_path, header)).directive_errors
    assert errors, f"{header.strip()!r} was dropped with no message"
    assert any(name in error and "line 2" in error for error in errors), errors


@pytest.mark.parametrize("header,name", REFUSED[:2])
def test_a_line_the_parser_cannot_read_fails_the_fixture(tmp_path, header, name):
    result = _run(_fixture(tmp_path, header))
    assert not result.total_success, (
        f"the fixture passed with {header.strip()!r} unread: {result.compilation_message}")
    assert name in result.compilation_message


@pytest.mark.parametrize("header", [
    "# CE2510: prose that names a diagnostic code\n",
    "# EXPECT_STDERR_EMPTY: true\n",
    "# EXPECT_NO_LEAKS\n",
    "# A sentence in plain prose.\n",
])
def test_a_line_that_is_prose_or_a_known_directive_is_no_error(tmp_path, header):
    assert parse_test_metadata(_fixture(tmp_path, header)).directive_errors == []


def test_a_code_prefixed_prose_line_passes_the_fixture(tmp_path):
    result = _run(_fixture(tmp_path, "# CE2510: prose that names a diagnostic code\n"))
    assert result.total_success, result.compilation_message
