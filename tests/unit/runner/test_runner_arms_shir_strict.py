"""The runner arms SUSHI_SHIR_STRICT=1 for every compile that it starts.

Under the variable, a SHIR hole of a correct program stops the compile with CE0155, so a
corpus run with the variable proves that lowering is total (Gate 1 of the SHIR migration).
The front end `tests/run_tests.py` sets it, as it sets the spelling gate. It is part of
how the runner checks, so no flag selects it off, and a value that the caller gives is
replaced. A user who starts `sushic` by hand does not get it.

The stub `fixtures/strict_stub.py` stands in for the compiler. It records the value that
each compile sees and the way that the compile started, in a child of the fork-server or
as a fresh process, and then runs the real compiler.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from _harness import FIXTURES_DIR, TESTS_DIR, run_tests
from fork_server import COMPILER_STUB_ENV

STUB = FIXTURES_DIR / "strict_stub.py"
LOG_ENV = "SUSHI_STRICT_PROBE_LOG"
STRICT_ENV = "SUSHI_SHIR_STRICT"
PROGRAM = ('# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n\n'
           'fn main() i32:\n    println("Mostly Harmless")\n    return 0\n')


@pytest.fixture
def staged():
    """A directory in the suite's discovery glob, with one fixture that prints."""
    marker = f"shir_strict_tmp_{os.getpid()}"
    directory = TESTS_DIR / marker
    directory.mkdir()
    (directory / "test_shir_strict_armed.sushi").write_text(PROGRAM, encoding="utf-8")
    try:
        yield marker
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _env(log: Path, strict: Optional[str]) -> dict[str, str]:
    env = {**os.environ, COMPILER_STUB_ENV: str(STUB), LOG_ENV: str(log)}
    env.pop(STRICT_ENV, None)
    if strict is not None:
        env[STRICT_ENV] = strict
    return env


def _seen(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_the_stub_records_an_unarmed_compile(tmp_path):
    """The control: a compile with no variable records `-`, so the tests below can fail."""
    log = tmp_path / "seen.txt"
    source = tmp_path / "main.sushi"
    source.write_text(PROGRAM, encoding="utf-8")
    env = _env(log, None)
    env["PYTHONPATH"] = str(TESTS_DIR.parent)
    subprocess.run([sys.executable, str(STUB), str(source), "-o", str(tmp_path / "main"),
                    "--cache-dir", str(tmp_path / "cache")],
                   cwd=TESTS_DIR.parent, env=env, capture_output=True, text=True,
                   timeout=300)
    assert _seen(log) == ["- fresh"], _seen(log)


@pytest.mark.parametrize("given", [None, "0"])
def test_a_compile_in_a_child_of_the_fork_server_sees_the_variable(staged, tmp_path, given):
    log = tmp_path / "seen.txt"
    done = run_tests("--skip-build", "--filter", staged, env=_env(log, given))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "Passed: 1" in done.stdout, done.stdout[-3000:]
    assert _seen(log) == ["1 forked"], _seen(log)


def test_a_compile_in_a_fresh_process_sees_the_variable(staged, tmp_path):
    log = tmp_path / "seen.txt"
    done = run_tests("--skip-build", "--fresh-processes", "--filter", staged,
                     env=_env(log, None))
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "Passed: 1" in done.stdout, done.stdout[-3000:]
    assert _seen(log) == ["1 fresh"], _seen(log)
