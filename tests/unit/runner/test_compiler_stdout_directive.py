"""`EXPECT_COMPILER_STDOUT_CONTAINS`, red and green.

The directive reads the stdout of the fixture's last compilation, the build report that
`EXPECT_REBUILT` and `EXPECT_CACHED` read. A unit that the compiler reuses and a unit
whose body passes ran both report `[cached]`; the `Front end:` line tells them apart.
"""
from __future__ import annotations

from pathlib import Path

from _harness import detail, run_single

MAIN = 'use "dep"\n\nfn main() i32:\n    println("{N}")\n    return 0\n'
DEP = "public const i32 N = 1\n"
PRINTS_1 = '# EXPECT_STDOUT_EXACT: "1\\n"\n'


def _fixture(tmp_path: Path, header: str) -> Path:
    home = tmp_path / "fixture"
    home.mkdir(parents=True)
    path = home / "test_report.sushi"
    path.write_text(PRINTS_1 + header + "\n" + MAIN, encoding="utf-8")
    (home / "dep.sushi").write_text(DEP, encoding="utf-8")
    return path


def test_a_text_of_the_report_holds(tmp_path):
    header = '# EXPECT_COMPILER_STDOUT_CONTAINS: "Codegen: 2 units"\n'
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)


def test_a_text_that_the_report_lacks_fails(tmp_path):
    header = '# EXPECT_COMPILER_STDOUT_CONTAINS: "Codegen: 3 units"\n'
    result = run_single(_fixture(tmp_path, header))
    assert not result.total_success
    assert "the compiler stdout lacks 'Codegen: 3 units'" in detail(result), detail(result)


def test_the_report_of_the_second_build_is_read(tmp_path):
    header = ("# SECOND_BUILD_IN: other\n"
              '# EXPECT_COMPILER_STDOUT_CONTAINS: "(2 cached, 0 rebuilt)"\n')
    result = run_single(_fixture(tmp_path, header))
    assert result.total_success, detail(result)
