"""A library build passes on exit 0 alone; `BUILD_LIB_WARNS` names the warnings of one that warns (#1122).

Exit 1 is a library built with a warning. When every library build accepted it, a fixture
could not fail on a warning that nobody expected. `BUILD_LIB_WARNS: x.sushi -> CW1002`
builds a library that must exit 1 and print exactly the named set of warning codes; a kind
word after the source (`x.sushi binary -> CW1002`) builds a binary or a hybrid one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _harness import run_single, detail
from test_metadata import parse_test_metadata

USES_LIB = 'use <lib/dep>\n\nfn main() i32:\n    println("{N}")\n    return 0\n'
OUT_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'
CLEAN = "public const i32 N = 1\n"
# CW1002: a variable shadows one of an outer scope.
SHADOWS = ("public const i32 N = 1\n\npublic fn doubled(i32 n) i32:\n    let i32 x = 2\n"
           "    if (n > 0):\n        let i32 x = 3\n        return n * x\n    return n * x\n")
# CW1002 and CW1001.
SHADOWS_AND_UNUSED = ("public const i32 N = 1\n\npublic fn doubled(i32 n) i32:\n"
                      "    let i32 x = 2\n    let i32 y = 1\n    if (n > 0):\n"
                      "        let i32 x = 3\n        return n * x\n    return n * x\n")


def _fixture(tmp_path: Path, header: str, dep: str) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / "dep.sushi").write_text(dep, encoding="utf-8")
    path = home / "test_lib_warns.sushi"
    path.write_text(header + OUT_1 + "\n" + USES_LIB, encoding="utf-8")
    return path


def test_the_directive_parses(tmp_path):
    meta = parse_test_metadata(_fixture(
        tmp_path, "# BUILD_LIB_WARNS: dep.sushi -> CW1002, CW1001\n", CLEAN))
    assert meta.directive_errors == []
    assert meta.build_libs_warns == [("dep.sushi", ["CW1002", "CW1001"])]
    assert meta.libs_on_the_path


def test_the_kind_word_parses(tmp_path):
    meta = parse_test_metadata(_fixture(
        tmp_path, "# BUILD_LIB_WARNS: dep.sushi binary -> CW1002\n", CLEAN))
    assert meta.directive_errors == []
    assert meta.library_builds == [("binary", "dep.sushi", "dep.slib", None, ["CW1002"])]


@pytest.mark.parametrize("value", ["dep.sushi", "dep.sushi ->", "-> CW1002",
                                   "dep.sushi -> CE1001", "a.sushi b.sushi -> CW1002",
                                   "dep.sushi binary extra -> CW1002"])
def test_a_bad_value_is_a_directive_error(tmp_path, value):
    meta = parse_test_metadata(_fixture(tmp_path, f"# BUILD_LIB_WARNS: {value}\n", CLEAN))
    assert meta.directive_errors, meta


@pytest.mark.parametrize("header, dep, passes", [
    ("# BUILD_LIB_WARNS: dep.sushi -> CW1002\n", SHADOWS, True),
    ("# BUILD_LIB_WARNS: dep.sushi -> CW1002, CW1001\n", SHADOWS_AND_UNUSED, True),
    ("# BUILD_LIB_WARNS: dep.sushi -> CW1002\n", SHADOWS_AND_UNUSED, False),
    ("# BUILD_LIB_WARNS: dep.sushi -> CW1002, CW1001\n", SHADOWS, False),
    ("# BUILD_LIB_WARNS: dep.sushi -> CW1002\n", CLEAN, False),
    ("# BUILD_LIB_WARNS: dep.sushi binary -> CW1002\n", SHADOWS, True),
    ("# BUILD_LIB_WARNS: dep.sushi hybrid -> CW1002\n", SHADOWS, True),
    ("# BUILD_LIB: dep.sushi\n", SHADOWS, False),
    ("# BUILD_LIB_BINARY: dep.sushi\n", SHADOWS, False),
    ("# BUILD_LIB: dep.sushi\n", CLEAN, True),
], ids=["the named code", "two named codes", "an extra code", "a missing code",
        "exit 0", "a binary library", "a hybrid library", "BUILD_LIB with a warning", "BUILD_LIB_BINARY with a warning",
        "control: BUILD_LIB with no warning"])
def test_the_library_build_decides_the_verdict(tmp_path, header, dep, passes):
    result = run_single(_fixture(tmp_path, header, dep))
    assert result.total_success is passes, detail(result)


def test_an_unexpected_warning_is_named(tmp_path):
    result = run_single(_fixture(tmp_path, "# BUILD_LIB: dep.sushi\n", SHADOWS))
    assert not result.total_success
    assert "BUILD_LIB dep.sushi failed with exit 1" in result.compilation_message, detail(result)


def test_a_wrong_warning_set_is_named(tmp_path):
    result = run_single(_fixture(tmp_path, "# BUILD_LIB_WARNS: dep.sushi -> CW1002\n",
                                 SHADOWS_AND_UNUSED))
    assert not result.total_success
    assert "not expected: CW1001" in result.compilation_message, detail(result)
