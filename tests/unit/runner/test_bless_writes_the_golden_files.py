"""`--bless` writes the golden files of the fixtures it selects, from the actual dumps.

A bless run selects the fixtures that carry `EXPECT_DUMP_SHIR` or `EXPECT_DUMP_SLIR`, and
it composes with the other selectors. It checks each of them in full, writes each dump to
its golden file, and prints each file it wrote; a run with no other fault exits 0. A run
without `--bless` never writes a golden file: a difference fails the fixture.

The dumps come from the stub of `test_golden_dump_directives.py`, because `--dump-shir` and
`--dump-slir` do not exist yet.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from _harness import FIXTURES_DIR, PROJECT_ROOT, TESTS_DIR, detail, run_single, run_tests
import enhanced_test_runner
from fork_server import COMPILER_STUB_ENV

STUB = FIXTURES_DIR / "dump_stub.py"
PROGRAM = ('# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n{header}\n'
           'fn main() i32:\n    println("Mostly Harmless")\n    return 0\n')
SHIR = "shir\nfn main() i32:\n"
SLIR = "slir\nfn main() i32:\n"
BOTH = "# EXPECT_DUMP_SHIR: main.shir\n# EXPECT_DUMP_SLIR: main.slir"


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setenv(COMPILER_STUB_ENV, str(STUB))


def _fixture(directory: Path, name: str, header: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(PROGRAM.format(header=header), encoding="utf-8")
    return path


def _bless(tests_dir: Path, bless: bool = True):
    with enhanced_test_runner.TestRunner(tests_dir, project_root=PROJECT_ROOT,
                                         parallel_jobs=1, bless=bless) as runner:
        return runner.run_all_tests(), runner


def test_bless_writes_the_missing_golden_files(stub, tmp_path):
    _fixture(tmp_path / "golden", "test_golden.sushi", BOTH)
    _fixture(tmp_path / "plain", "test_plain.sushi", "")
    results, runner = _bless(tmp_path)
    assert list(results) == ["golden/test_golden.sushi"], "bless selects the golden fixtures"
    assert all(r.total_success for r in results.values()), {
        k: detail(r) for k, r in results.items()}
    assert (tmp_path / "golden" / "main.shir").read_text(encoding="utf-8") == SHIR
    assert (tmp_path / "golden" / "main.slir").read_text(encoding="utf-8") == SLIR
    assert sorted(runner.blessed_files()) == [tmp_path / "golden" / "main.shir",
                                              tmp_path / "golden" / "main.slir"]


def test_bless_rewrites_a_stale_file_and_a_plain_run_then_passes(stub, tmp_path):
    home = tmp_path / "golden"
    _fixture(home, "test_golden.sushi", BOTH)
    (home / "main.shir").write_text("stale\n", encoding="utf-8")
    (home / "main.slir").write_text(SLIR, encoding="utf-8")
    _, runner = _bless(tmp_path)
    assert (home / "main.shir").read_text(encoding="utf-8") == SHIR
    assert runner.blessed_files() == [home / "main.shir"], "an unchanged file is not written"
    results, _ = _bless(tmp_path, bless=False)
    assert all(r.total_success for r in results.values()), {
        k: detail(r) for k, r in results.items()}


def test_a_plain_run_never_writes_a_golden_file(stub, tmp_path):
    home = tmp_path / "golden"
    _fixture(home, "test_golden.sushi", "# EXPECT_DUMP_SHIR: main.shir")
    (home / "main.shir").write_text("stale\n", encoding="utf-8")
    results, runner = _bless(tmp_path, bless=False)
    assert [r.total_success for r in results.values()] == [False]
    assert (home / "main.shir").read_text(encoding="utf-8") == "stale\n"
    assert runner.blessed_files() == []


def test_a_missing_golden_file_names_bless(stub, tmp_path):
    result = run_single(_fixture(tmp_path, "test_golden.sushi", "# EXPECT_DUMP_SHIR: main.shir"))
    assert not result.total_success
    assert "main.shir" in result.compilation_message, detail(result)
    assert "--bless" in result.compilation_message, detail(result)


def test_a_dump_the_compiler_did_not_write_is_not_blessed(stub, tmp_path):
    home = tmp_path / "golden"
    path = _fixture(home, "test_golden.sushi", "# EXPECT_DUMP_SHIR: main.shir")
    path.write_text(path.read_text(encoding="utf-8").replace(
        "    return 0", "    # no dump\n    return 0"), encoding="utf-8")
    results, runner = _bless(tmp_path)
    assert [r.total_success for r in results.values()] == [False]
    assert not (home / "main.shir").exists() and runner.blessed_files() == []


@pytest.fixture
def staged():
    """A golden fixture in the suite's discovery glob, under a directory of its own."""
    marker = f"golden_bless_tmp_{os.getpid()}"
    home = TESTS_DIR / marker
    try:
        _fixture(home, "test_golden_bless_probe.sushi", BOTH)
        yield marker
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_front_end_blesses_and_prints_each_file(staged):
    env = {**os.environ, COMPILER_STUB_ENV: str(STUB)}
    done = run_tests("--skip-build", "--bless", "--filter", staged, env=env)
    assert done.returncode == 0, done.stdout[-3000:]
    for name in ("main.shir", "main.slir"):
        assert f"Blessed: tests/{staged}/{name}" in done.stdout, done.stdout[-3000:]
    assert (TESTS_DIR / staged / "main.shir").read_text(encoding="utf-8") == SHIR
    assert "Stdlib doc-block gate SKIPPED: --bless" in done.stdout, done.stdout[-3000:]
    again = run_tests("--skip-build", "--filter", staged, env=env)
    assert again.returncode == 0, again.stdout[-3000:]
    assert "Blessed:" not in again.stdout, "a plain run blesses nothing"


def test_bless_over_a_selection_with_no_golden_fixture_fails():
    done = run_tests("--skip-build", "--bless", "--filter", "diagnostics/borrow_help/")
    assert done.returncode == 1, done.stdout[-3000:]
    assert "--bless" in done.stdout, done.stdout[-3000:]
