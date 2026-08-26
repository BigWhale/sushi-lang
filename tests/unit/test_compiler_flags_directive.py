"""`COMPILER_FLAGS:`, the directive that lets a `.sushi` fixture turn a compiler flag on.

R36: a diagnostic behind a flag had no fixture before this. `test_warn_missing_docs.sushi`
would compile clean and fail its own naming contract, because the runner always spelled
the same command line. The directive appends to it, and the next flag-gated diagnostic
gets the facility for nothing.

A flag the RUNNER owns is refused: it decides the output path, the build kind and the
cache, and a fixture that changed one of those would break the run rather than the test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_metadata import parse_test_metadata  # noqa: E402


def _fixture(tmp_path: Path, header: str, name: str = "test_flags.sushi") -> Path:
    path = tmp_path / name
    path.write_text(header + "\nfn main() i32:\n    return Result.Ok(0)\n",
                    encoding="utf-8")
    return path


def test_one_flag_parses(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# COMPILER_FLAGS: --warn-missing-docs\n"))
    assert meta.compiler_flags == ["--warn-missing-docs"]


def test_several_flags_on_one_line_parse(tmp_path):
    meta = parse_test_metadata(
        _fixture(tmp_path, "# COMPILER_FLAGS: --warn-missing-docs --no-verify\n"))
    assert meta.compiler_flags == ["--warn-missing-docs", "--no-verify"]


def test_the_directive_may_be_repeated(tmp_path):
    meta = parse_test_metadata(_fixture(
        tmp_path,
        "# COMPILER_FLAGS: --warn-missing-docs\n# COMPILER_FLAGS: --no-verify\n"))
    assert meta.compiler_flags == ["--warn-missing-docs", "--no-verify"]


def test_a_file_with_no_directive_carries_no_flags(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# Test: nothing to declare.\n"))
    assert meta.compiler_flags == []


@pytest.mark.parametrize("flag", [
    "-o", "--lib", "--lib-info", "--clean-cache", "--build-stdlib", "--cache-dir",
])
def test_a_runner_owned_flag_is_refused(tmp_path, capsys, flag):
    meta = parse_test_metadata(_fixture(tmp_path, f"# COMPILER_FLAGS: {flag}\n"))
    assert meta.compiler_flags == []
    assert flag in capsys.readouterr().out


def test_a_refused_flag_does_not_take_the_others_with_it(tmp_path, capsys):
    meta = parse_test_metadata(
        _fixture(tmp_path, "# COMPILER_FLAGS: --warn-missing-docs --lib\n"))
    assert meta.compiler_flags == ["--warn-missing-docs"]
    assert "--lib" in capsys.readouterr().out


def test_a_directive_below_the_header_block_is_not_read(tmp_path):
    """Every directive lives in the LEADING comment block, and this one is no different."""
    path = tmp_path / "test_flags.sushi"
    path.write_text("fn main() i32:\n    # COMPILER_FLAGS: --warn-missing-docs\n"
                    "    return Result.Ok(0)\n", encoding="utf-8")
    assert parse_test_metadata(path).compiler_flags == []
