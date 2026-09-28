"""The user's-directory directives, red and green, through the real runner (#976, #974, #971).

`BUILD_LIB_AT` builds a source `.slib` at a path inside the fixture's copy and adds nothing
to `SUSHI_LIB_PATH`. `FIXTURE_CACHE_DIR` passes a RELATIVE `--cache-dir` to a fixture that
runs in its own directory. `EXPECT_PATH_EXISTS` / `EXPECT_PATH_ABSENT` read the copy after
the fixture's last compiler invocation. `THEN_CLEAN_CACHE` is that last invocation: a
`sushic --clean-cache`, bare or with the source, in the copy.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from _harness import TESTS_DIR, run_single, detail
import enhanced_test_runner
from test_metadata import parse_test_metadata

MAIN = 'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return Result.Ok(0)\n'
USES_LIB = 'use <lib/dep>\n\nfn main() i32:\n    println("{N}")\n    return Result.Ok(0)\n'
DEP = "public const i32 N = 1\n"
NORI = '[package]\nname = "fixture"\nversion = "0.1.0"\n'


def _fixture(tmp_path: Path, header: str, body: str = MAIN,
             extra: dict | None = None) -> Path:
    home = tmp_path / "fixture"
    home.mkdir()
    (home / "dep.sushi").write_text(DEP, encoding="utf-8")
    for rel, text in (extra or {}).items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text, encoding="utf-8")
    path = home / "test_paths.sushi"
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


def _spy_sushic(monkeypatch) -> list[tuple[list[str], dict]]:
    seen: list[tuple[list[str], dict]] = []
    real_run = enhanced_test_runner.subprocess.run

    def _spy(cmd, *args, **kwargs):
        if any(str(c).endswith("sushic") for c in cmd):
            seen.append(([str(c) for c in cmd], dict(kwargs.get("env") or {})))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(enhanced_test_runner.subprocess, "run", _spy)
    return seen


OUT_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'
IN_DIR = "# RUN_IN_FIXTURE_DIR\n"


def test_the_directives_parse(tmp_path):
    path = _fixture(tmp_path, IN_DIR
                    + "# BUILD_LIB_AT: a.sushi -> a.slib\n"
                    + "# BUILD_LIB_AT: b.sushi -> .sushi_bento/b/lib/b.slib\n"
                    + "# FIXTURE_CACHE_DIR: rel\n"
                    + "# EXPECT_PATH_EXISTS: rel/units, rel/libsrc\n"
                    + "# EXPECT_PATH_EXISTS: x\n"
                    + "# EXPECT_PATH_ABSENT: __sushi_cache__\n"
                    + "# EXPECT_PATH_EXISTS_BEFORE_CLEAN: rel/units\n"
                    + "# THEN_CLEAN_CACHE: source\n")
    meta = parse_test_metadata(path)
    assert meta.build_libs_at == [("a.sushi", "a.slib"),
                                  ("b.sushi", ".sushi_bento/b/lib/b.slib")]
    assert meta.fixture_cache_dir == "rel"
    assert meta.expect_paths_exist == ["rel/units", "rel/libsrc", "x"]
    assert meta.expect_paths_absent == ["__sushi_cache__"]
    assert meta.expect_paths_exist_before_clean == ["rel/units"]
    assert meta.then_clean_cache == "source"
    assert meta.directive_errors == []


def test_no_directive_is_nothing(tmp_path):
    meta = parse_test_metadata(_fixture(tmp_path, OUT_1))
    assert meta.build_libs_at == [] and meta.fixture_cache_dir is None
    assert meta.expect_paths_exist == [] and meta.expect_paths_absent == []
    assert meta.expect_paths_exist_before_clean == []
    assert meta.then_clean_cache is None


# -- BUILD_LIB_AT --------------------------------------------------------------

@pytest.mark.parametrize("header, extra", [
    (IN_DIR + "# BUILD_LIB_AT: dep.sushi -> dep.slib\n", {}),
    (IN_DIR + "# BUILD_LIB_AT: dep.sushi -> .sushi_bento/dep/lib/dep.slib\n",
     {"nori.toml": NORI}),
], ids=["next to the program", "in the project's .sushi_bento"])
def test_build_lib_at_builds_where_the_fixture_says(tmp_path, monkeypatch, header, extra):
    seen = _spy_sushic(monkeypatch)
    target = header.split("-> ")[1].strip()
    path = _fixture(tmp_path, header + OUT_1 + f"# EXPECT_PATH_EXISTS: {target}\n",
                    USES_LIB, extra)
    result = run_single(path)
    assert result.total_success, detail(result)
    compile_env = [env for cmd, env in seen if "--lib" not in cmd][-1]
    assert compile_env.get("SUSHI_LIB_PATH") == os.environ.get("SUSHI_LIB_PATH"), \
        "BUILD_LIB_AT adds nothing to SUSHI_LIB_PATH"


def test_without_build_lib_at_the_library_is_missing(tmp_path):
    result = run_single(_fixture(tmp_path, IN_DIR + OUT_1, USES_LIB))
    assert not result.total_success
    assert "CE3502" in detail(result), detail(result)


@pytest.mark.parametrize("value, says", [
    ("dep.sushi", "BUILD_LIB_AT"),
    ("dep.sushi -> ../dep.slib", "inside the fixture"),
    ("dep.sushi -> /tmp/dep.slib", "inside the fixture"),
], ids=["no target", "a target outside the copy", "an absolute target"])
def test_a_bad_build_lib_at_fails_the_fixture(tmp_path, value, says):
    result = run_single(_fixture(tmp_path, IN_DIR + f"# BUILD_LIB_AT: {value}\n" + OUT_1, USES_LIB))
    assert not result.total_success
    assert says in result.compilation_message, result.compilation_message


def test_a_build_lib_at_that_does_not_build_fails_the_fixture(tmp_path):
    path = _fixture(tmp_path, IN_DIR + "# BUILD_LIB_AT: dep.sushi -> dep.slib\n" + OUT_1,
                    USES_LIB)
    (path.parent / "dep.sushi").write_text("public const i32 N = \n", encoding="utf-8")
    result = run_single(path)
    assert not result.total_success
    assert "BUILD_LIB_AT dep.sushi failed" in result.compilation_message, \
        result.compilation_message


# -- FIXTURE_CACHE_DIR ---------------------------------------------------------

def test_fixture_cache_dir_passes_the_relative_spelling(tmp_path, monkeypatch):
    seen = _spy_sushic(monkeypatch)
    header = (IN_DIR + "# FIXTURE_CACHE_DIR: rel\n" + OUT_1
              + "# EXPECT_PATH_EXISTS: rel/units\n# EXPECT_PATH_ABSENT: __sushi_cache__\n")
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)
    (cmd, _), = seen
    assert cmd[cmd.index("--cache-dir") + 1] == "rel", cmd


def test_fixture_cache_dir_moves_the_cache(tmp_path):
    header = IN_DIR + "# FIXTURE_CACHE_DIR: rel\n" + OUT_1 + "# EXPECT_PATH_EXISTS: __sushi_cache__\n"
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "__sushi_cache__" in result.compilation_message, result.compilation_message


@pytest.mark.parametrize("header, says", [
    ("# FIXTURE_CACHE_DIR: rel\n", "RUN_IN_FIXTURE_DIR"),
    (IN_DIR + "# FIXTURE_CACHE_DIR: /tmp/rel\n", "inside the fixture"),
    (IN_DIR + "# FIXTURE_CACHE_DIR: ../rel\n", "inside the fixture"),
], ids=["not in the fixture's directory", "an absolute path", "a path outside the copy"])
def test_a_bad_fixture_cache_dir_fails_the_fixture(tmp_path, header, says):
    result = run_single(_fixture(tmp_path, header + OUT_1))
    assert not result.total_success
    assert says in result.compilation_message, result.compilation_message


# -- EXPECT_PATH_EXISTS / EXPECT_PATH_ABSENT ------------------------------------

def test_path_expectations_that_hold_pass(tmp_path):
    header = (IN_DIR + OUT_1 + "# EXPECT_PATH_EXISTS: __sushi_cache__/units, dep.sushi\n"
              "# EXPECT_PATH_ABSENT: nothing_here\n")
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)


def test_path_expectations_read_the_copy_without_run_in_fixture_dir(tmp_path):
    result = run_single(_fixture(tmp_path, OUT_1 + "# EXPECT_PATH_EXISTS: dep.sushi\n"
                                             "# EXPECT_PATH_ABSENT: __sushi_cache__\n"))
    assert result.total_success, detail(result)


@pytest.mark.parametrize("line, says", [
    ("# EXPECT_PATH_EXISTS: nothing_here\n", "nothing_here"),
    ("# EXPECT_PATH_ABSENT: __sushi_cache__\n", "__sushi_cache__"),
    ("# EXPECT_PATH_EXISTS: ../fixture\n", "inside the fixture"),
], ids=["a missing path", "a present path", "a path outside the copy"])
def test_path_expectations_that_do_not_hold_fail(tmp_path, line, says):
    result = run_single(_fixture(tmp_path, IN_DIR + OUT_1 + line))
    assert not result.total_success
    assert says in result.compilation_message, result.compilation_message


# -- THEN_CLEAN_CACHE ----------------------------------------------------------

def test_then_clean_cache_bare_removes_the_cache(tmp_path, monkeypatch):
    seen = _spy_sushic(monkeypatch)
    header = IN_DIR + OUT_1 + "# THEN_CLEAN_CACHE: bare\n# EXPECT_PATH_ABSENT: __sushi_cache__\n"
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)
    assert seen[-1][0][1:] == ["--clean-cache"], seen[-1][0]


def test_without_then_clean_cache_the_cache_stays(tmp_path):
    header = IN_DIR + OUT_1 + "# EXPECT_PATH_ABSENT: __sushi_cache__\n"
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "__sushi_cache__" in result.compilation_message, result.compilation_message


def test_then_clean_cache_bare_passes_the_fixture_cache_dir(tmp_path, monkeypatch):
    seen = _spy_sushic(monkeypatch)
    header = (IN_DIR + "# FIXTURE_CACHE_DIR: rel\n" + OUT_1 + "# THEN_CLEAN_CACHE: bare\n"
              "# EXPECT_PATH_ABSENT: rel, __sushi_cache__\n")
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)
    assert seen[-1][0][1:] == ["--clean-cache", "--cache-dir", "rel"], seen[-1][0]


def test_then_clean_cache_source_cleans_and_builds_again(tmp_path, monkeypatch):
    seen = _spy_sushic(monkeypatch)
    header = (IN_DIR + OUT_1 + "# THEN_CLEAN_CACHE: source\n"
              "# EXPECT_REBUILT: dep, test_paths\n# EXPECT_PATH_EXISTS: __sushi_cache__\n")
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)
    last = seen[-1][0]
    assert last[1:3] == ["--clean-cache", "test_paths.sushi"] and "-o" in last, last


def test_then_clean_cache_source_reads_the_report_of_the_clean_build(tmp_path):
    """Without the clean the second build would serve both units from the cache."""
    header = (IN_DIR + OUT_1 + "# THEN_CLEAN_CACHE: source\n"
              "# EXPECT_CACHED: dep, test_paths\n")
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "expected [cached]" in result.compilation_message, result.compilation_message


@pytest.mark.parametrize("header, says", [
    (IN_DIR + "# THEN_CLEAN_CACHE: twice\n", "THEN_CLEAN_CACHE"),
    ("# THEN_CLEAN_CACHE: bare\n", "RUN_IN_FIXTURE_DIR"),
], ids=["an unknown form", "not in the fixture's directory"])
def test_a_bad_then_clean_cache_fails_the_fixture(tmp_path, header, says):
    result = run_single(_fixture(tmp_path, header + OUT_1))
    assert not result.total_success
    assert says in result.compilation_message, result.compilation_message


@pytest.mark.parametrize("line, passes", [
    ("# EXPECT_PATH_EXISTS_BEFORE_CLEAN: __sushi_cache__/units\n", True),
    ("# EXPECT_PATH_EXISTS_BEFORE_CLEAN: nothing_here\n", False),
], ids=["a path the compilation wrote", "a path nothing wrote"])
def test_path_before_the_clean_is_read_before_it(tmp_path, line, passes):
    header = IN_DIR + OUT_1 + "# THEN_CLEAN_CACHE: bare\n# EXPECT_PATH_ABSENT: __sushi_cache__\n"
    result = run_single(_fixture(tmp_path, header + line))
    assert result.total_success is passes, detail(result)
    if not passes:
        assert "before THEN_CLEAN_CACHE" in result.compilation_message, \
            result.compilation_message


def test_a_path_before_the_clean_needs_the_clean(tmp_path):
    header = IN_DIR + OUT_1 + "# EXPECT_PATH_EXISTS_BEFORE_CLEAN: __sushi_cache__\n"
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "needs THEN_CLEAN_CACHE" in result.compilation_message, result.compilation_message


@pytest.mark.writes_the_checkout  # it writes into the checkout's `__sushi_cache__/`
def test_a_bare_clean_never_removes_the_checkouts_cache(tmp_path):
    """The runner fails a bare clean after which the checkout's own cache is gone.

    The marker is a directory of this test's own under the checkout's `__sushi_cache__/`;
    a correct compiler never touches that directory, so no other fixture races it.
    """
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        cache = runner.project_root / "__sushi_cache__"
        existed = cache.exists()
        marker = cache / f"runner_marker_{uuid.uuid4().hex}"
        marker.mkdir(parents=True)
        try:
            header = IN_DIR + OUT_1 + "# THEN_CLEAN_CACHE: bare\n"
            result = runner.run_single_test(_fixture(tmp_path, header))
            assert marker.is_dir(), "the bare --clean-cache removed the checkout's own cache"
            assert result.total_success, detail(result)
        finally:
            if marker.is_dir():
                marker.rmdir()
            if not existed and cache.is_dir() and not any(cache.iterdir()):
                cache.rmdir()
