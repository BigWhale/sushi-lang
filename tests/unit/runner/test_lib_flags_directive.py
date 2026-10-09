"""`LIB_FLAGS:`, the directive that gives the flags of a library build.

The flags belong to the library builds of a fixture and never to the build of the fixture
itself, which has `COMPILER_FLAGS:`. A flag the RUNNER owns is refused, and so are the two
flags the runner writes for each library build, because a later one would win silently.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_metadata import parse_test_metadata


def _fixture(tmp_path: Path, header: str, name: str = "test_lib_flags.sushi") -> Path:
    path = tmp_path / name
    path.write_text(header + "\nfn main() i32:\n    return 0\n", encoding="utf-8")
    return path


def test_one_flag_parses(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# LIB_FLAGS: --dont-panic\n"))
    assert meta.lib_flags == ["--dont-panic"]


def test_the_directive_may_be_repeated(tmp_path):
    meta = parse_test_metadata(
        _fixture(tmp_path, "# LIB_FLAGS: --dont-panic\n# LIB_FLAGS: --no-verify\n"))
    assert meta.lib_flags == ["--dont-panic", "--no-verify"]


def test_the_flags_reach_the_library_build_and_never_the_consumer_build(tmp_path):
    meta = parse_test_metadata(
        _fixture(tmp_path, "# LIB_FLAGS: --dont-panic\n# COMPILER_FLAGS: --no-verify\n"))
    assert meta.lib_flags == ["--dont-panic"]
    assert meta.compiler_flags == ["--no-verify"]


def test_a_file_with_no_directive_carries_no_lib_flags(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, "# Test: nothing to declare.\n"))
    assert meta.lib_flags == []


@pytest.mark.parametrize("flag", [
    "-o", "--lib", "--lib-info", "--clean-cache", "--build-stdlib", "--cache-dir",
    "--lib-kind", "--lib-version",
])
def test_a_runner_owned_flag_is_refused(tmp_path, capsys, flag):
    meta = parse_test_metadata(_fixture(tmp_path, f"# LIB_FLAGS: {flag}\n"))
    assert meta.lib_flags == []
    assert flag in capsys.readouterr().err


def test_a_refused_flag_does_not_take_the_others_with_it(tmp_path, capsys):
    meta = parse_test_metadata(_fixture(tmp_path, "# LIB_FLAGS: --dont-panic --lib\n"))
    assert meta.lib_flags == ["--dont-panic"]
    assert "--lib" in capsys.readouterr().err
