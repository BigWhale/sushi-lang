"""`--json` stdout carries the report and nothing else.

The badge job pipes `tests/run_tests.py --enhanced --json` straight into
`corpus-results.json`, so one stray line on stdout makes the file unparseable and the
badges go unpublished. The runner guards its own chatter behind `if not args.json`, but
`parse_test_metadata` runs during collection and knows nothing about the mode. Its
warnings are diagnostics, so they belong on stderr in every mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_ROOT))

from test_metadata import parse_test_metadata  # noqa: E402


# One case per warning `parse_test_metadata` can print. Each body is a valid test file
# apart from the one directive under test, so nothing else can be the source.
BAD_HEADERS = [
    pytest.param("# EXPECT_RUNTIME_EXIT: nope\n", id="runtime-exit"),
    pytest.param("# TIMEOUT_SECONDS: soon\n", id="timeout"),
    pytest.param("# TEST_TYPE: compile_error\n", id="test-type"),
    pytest.param("# TEST_ENV: sideways\n", id="test-env"),
    pytest.param("# COMPILER_FLAGS: --clean-cache\n", id="runner-owned-flag"),
]


def _write(tmp_path: Path, header: str) -> Path:
    path = tmp_path / "test_probe.sushi"
    path.write_text(header + "\nfn main() i32:\n    return Result.Ok(0)\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("header", BAD_HEADERS)
def test_a_directive_warning_goes_to_stderr(tmp_path, capsys, header):
    parse_test_metadata(_write(tmp_path, header))

    captured = capsys.readouterr()
    assert "Warning" in captured.err, "the warning must still be visible to a human"
    assert captured.out == "", (
        "this warning lands on stdout, where --json puts the corpus report:\n"
        + captured.out)


def test_an_unreadable_file_warns_on_stderr(tmp_path, capsys):
    """The catch-all around the whole parse is the sixth writer, and the same rule."""
    path = tmp_path / "test_probe.sushi"
    path.write_bytes(b"# TEST_TYPE: runtime\n\xff\xfe not utf-8\n")

    parse_test_metadata(path)

    captured = capsys.readouterr()
    assert "Warning" in captured.err
    assert captured.out == ""
