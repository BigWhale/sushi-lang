"""`FIXTURE_SUSHI_HOME`, `SECOND_BUILD_IN` and the run's own per-user home, red and green.

The compiler keeps the objects of the bundled stdlib units in the per-user cache,
`$SUSHI_HOME/cache/compiler/`. A run gives every compile a home of its own, in its temporary
directory, so a run never writes the user's home. `FIXTURE_SUSHI_HOME` gives one fixture a
home in its copy. `SECOND_BUILD_IN` compiles the fixture a second time from another
directory of the copy, and the unit report of that build is the one that `EXPECT_CACHED`
and `EXPECT_REBUILT` read.
"""
from __future__ import annotations

from pathlib import Path

from _harness import TESTS_DIR, detail, run_single
import enhanced_test_runner
from test_metadata import parse_test_metadata

MAIN = 'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return 0\n'
DEP = "public const i32 N = 1\n"
PROCESS = ('use <sys/process>\n\nfn main() i32:\n    if (getpid() > 0):\n'
           '        println("1")\n    return 0\n')
PRINTS_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'


def _fixture(tmp_path: Path, header: str, body: str = MAIN, v2: bool = False) -> Path:
    home = tmp_path / "fixture"
    home.mkdir(parents=True)
    path = home / "test_home.sushi"
    path.write_text(header + "\n" + body, encoding="utf-8")
    (home / "dep.sushi").write_text(DEP, encoding="utf-8")
    if v2:
        (home / "v2").mkdir()
        (home / "v2" / "dep.sushi").write_text(DEP, encoding="utf-8")
    return path


def test_a_run_has_a_home_of_its_own():
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        home = Path(runner.env_overrides["SUSHI_HOME"])
        assert home.is_relative_to(Path(runner.temp_dir)), home


def test_the_fixture_home_holds_the_compiler_cache(tmp_path):
    header = (PRINTS_1 + "# FIXTURE_SUSHI_HOME: home\n"
              "# EXPECT_PATH_EXISTS: home/cache/compiler/grammar\n")
    result = run_single(_fixture(tmp_path, header, PROCESS))
    assert result.total_success, detail(result)


def test_a_fixture_with_no_home_of_its_own_writes_none_in_its_copy(tmp_path):
    header = PRINTS_1 + "# EXPECT_PATH_EXISTS: home\n"
    result = run_single(_fixture(tmp_path, header, PROCESS))
    assert not result.total_success
    assert "home: expected to exist, missing" in detail(result), detail(result)


def test_a_second_build_reads_the_cache_of_the_first(tmp_path):
    header = PRINTS_1 + "# SECOND_BUILD_IN: other/dir\n# EXPECT_CACHED: dep, test_home\n"
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)


def test_the_report_of_the_second_build_is_read(tmp_path):
    header = PRINTS_1 + "# SECOND_BUILD_IN: other\n# EXPECT_REBUILT: dep, test_home\n"
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "dep: expected [rebuilt], got [cached]" in detail(result), detail(result)


def test_a_second_build_does_not_go_with_v2(tmp_path):
    header = PRINTS_1 + "# SECOND_BUILD_IN: other\n# EXPECT_CACHED: dep, test_home\n"
    result = run_single(_fixture(tmp_path, header, v2=True))
    assert not result.total_success
    assert "SECOND_BUILD_IN" in result.compilation_message, result.compilation_message


def test_a_path_outside_the_copy_is_refused(tmp_path):
    for header in ("# FIXTURE_SUSHI_HOME: /abs\n", "# SECOND_BUILD_IN: ../up\n"):
        path = _fixture(tmp_path / header[2:8], PRINTS_1 + header)
        assert parse_test_metadata(path).directive_errors, header
        assert not run_single(path).total_success, header
