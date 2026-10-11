"""The runner step `golden_seed_gate`: each golden dump must not change with the hash seed.

A golden file that flakes trains people to bless it again (docs/design/ir.md, 11.3). The
fork-server fixes its hash seed when it starts, so after the main run the step starts a
SECOND fork-server with another pinned PYTHONHASHSEED, compiles each golden fixture of the
selection again under it, and compares each dump with its golden file again. The main run
keeps the seed of the runner, and the step prints both seeds. A selection with no golden
fixture prints the skip.

The dumps come from the stub of `test_golden_dump_directives.py`. Its `# unstable dump`
mode prints words in the order of a set, which follows the hash seed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _harness import FIXTURES_DIR, PROJECT_ROOT, TESTS_DIR, run_tests
from enhanced_test_runner import golden_seed_gate
from fork_server import COMPILER_STUB_ENV
from golden_dumps import gate_seed

STUB = FIXTURES_DIR / "dump_stub.py"
STABLE = ('# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n# EXPECT_DUMP_SHIR: main.shir\n\n'
          'fn main() i32:\n    println("Mostly Harmless")\n    return 0\n')
UNSTABLE = STABLE.replace("    return 0", "    # unstable dump\n    return 0")


def _dump_under(seed: str, source: str) -> str:
    """The stub's dump of `source` in a process with the hash seed `seed`."""
    code = "import sys; from dump_stub import dump; sys.stdout.write(dump('shir', sys.argv[1]))"
    return subprocess.run([sys.executable, "-c", code, source],
                          capture_output=True, text=True, timeout=60, check=True,
                          env={**os.environ, "PYTHONHASHSEED": seed,
                               "PYTHONPATH": str(FIXTURES_DIR)}).stdout


def _golden(directory: Path, source: str) -> Path:
    """A golden fixture whose golden file holds the dump under the main seed, 0."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "test_golden_seed.sushi"
    path.write_text(source, encoding="utf-8")
    (directory / "main.shir").write_text(_dump_under("0", source), encoding="utf-8")
    return path


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setenv(COMPILER_STUB_ENV, str(STUB))
    monkeypatch.setenv("PYTHONHASHSEED", "0")


def test_the_unstable_dump_follows_the_seed():
    """The control: without it the red tests below prove nothing."""
    assert _dump_under("0", UNSTABLE) != _dump_under("1", UNSTABLE)
    assert _dump_under("0", STABLE) == _dump_under("1", STABLE)


def test_the_gate_seed_is_pinned_and_differs_from_the_main_seed():
    assert gate_seed(None) == "1"
    assert gate_seed("0") == "1"
    assert gate_seed("1") == "2"
    assert gate_seed("random") == "1"


def test_a_stable_dump_passes_the_gate(seeded, tmp_path):
    fixture = _golden(tmp_path / "stable", STABLE)
    result = golden_seed_gate(tmp_path, [fixture], jobs=1, project_root=PROJECT_ROOT)
    assert result.ran and result.passed, result.report()
    assert result.modules == ["stable/test_golden_seed.sushi"], result.modules


def test_an_unstable_dump_fails_the_gate(seeded, tmp_path):
    fixture = _golden(tmp_path / "unstable", UNSTABLE)
    result = golden_seed_gate(tmp_path, [fixture], jobs=1, project_root=PROJECT_ROOT)
    assert result.ran and not result.passed, result.report()
    assert list(result.failures) == ["unstable/test_golden_seed.sushi"], result.failures
    assert any("main.shir" in line for line in result.failures[
        "unstable/test_golden_seed.sushi"]), result.failures


def test_a_selection_with_no_golden_fixture_skips(seeded, tmp_path):
    plain = tmp_path / "test_plain.sushi"
    plain.write_text(STABLE.replace("# EXPECT_DUMP_SHIR: main.shir\n", ""), encoding="utf-8")
    result = golden_seed_gate(tmp_path, [plain], jobs=1, project_root=PROJECT_ROOT)
    assert not result.ran
    assert result.report() == [
        "Golden seed gate SKIPPED: the selection holds no golden fixture."]


@pytest.fixture
def staged():
    """A directory in the suite's discovery glob, for the golden fixtures of one test."""
    marker = f"golden_seed_tmp_{os.getpid()}"
    try:
        yield marker
    finally:
        shutil.rmtree(TESTS_DIR / marker, ignore_errors=True)


def _env() -> dict[str, str]:
    return {**os.environ, COMPILER_STUB_ENV: str(STUB), "PYTHONHASHSEED": "0"}


def test_the_front_end_prints_both_seeds(staged):
    _golden(TESTS_DIR / staged, STABLE)
    done = run_tests("--skip-build", "--filter", staged, env=_env())
    assert done.returncode == 0, done.stdout[-3000:]
    assert ("Golden seed gate (main seed 0, gate seed 1): 1 golden fixture(s), 0 failed"
            in done.stdout), done.stdout[-3000:]


def test_the_front_end_fails_a_run_with_an_unstable_dump(staged):
    _golden(TESTS_DIR / staged, UNSTABLE)
    done = run_tests("--skip-build", "--filter", staged, env=_env())
    assert "Passed: 1" in done.stdout, "the main run keeps seed 0:\n" + done.stdout[-3000:]
    assert done.returncode == 1, done.stdout[-3000:]
    assert ("Golden seed gate (main seed 0, gate seed 1): 1 golden fixture(s), 1 failed"
            in done.stdout), done.stdout[-3000:]


def test_the_gate_report_keeps_json_stdout_clean(staged):
    _golden(TESTS_DIR / staged, STABLE)
    done = run_tests("--skip-build", "--json", "--filter", staged, env=_env())
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert json.loads(done.stdout)["passed"] == 1
    assert "Golden seed gate (main seed 0, gate seed 1)" in done.stderr, done.stderr[-3000:]


def test_a_front_end_selection_with_no_golden_fixture_prints_the_skip():
    done = run_tests("--skip-build", "--filter", "diagnostics/borrow_help/")
    assert done.returncode == 0, done.stdout[-3000:]
    assert ("Golden seed gate SKIPPED: the selection holds no golden fixture."
            in done.stdout), done.stdout[-3000:]
