"""The runner's dead-code gate (#959, ruling 5): a runner step, so these tests start the
runner, the one exception to the rule that pytest never runs the Sushi compiler.

The gate compiles the program that imports every stdlib module, with `--warn-unused` and
SUSHI_STDLIB_DEAD_GATE set, and each program under `toolchain/src/` with `--warn-unused`,
and fails the run on a CW1004 or CW3006 located in a checked file. `SUSHI_DOC_GATE_MODULES`
points it at a synthetic stdlib module and `SUSHI_DEAD_GATE_PROGRAMS` at a synthetic
program. A `--filter` selects each by a substring of its path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from _harness import PROJECT_ROOT, run_tests
from run_tests import (
    DEAD_GATE_PROGRAMS_ENV, DOC_GATE_MODULES_ENV, dead_gate_programs,
)

ONE_FIXTURE = "diagnostics/unused/"
FIXTURES_IN_IT = 3

DEAD_MODULE = """\
##: A synthetic module for the dead-code gate. :##

use <time>

##:
Gives the answer to life, the universe and everything.

- Returns: The answer.
:##
fn forgotten_answer() i32:
    return Result.Ok(42)

##:
Gives a number.

- Returns: The number.
:##
public fn live_answer() i32:
    return Result.Ok(7)
"""

LIVE_MODULE = """\
##: A synthetic module for the dead-code gate. :##

##:
Gives the answer to life, the universe and everything.

- Returns: The answer.
:##
fn private_answer() i32:
    return Result.Ok(42)

##:
Gives the answer through a private helper.

- Returns: The answer.
:##
public fn public_answer() i32:
    return Result.Ok(private_answer()??)
"""

DEAD_PROGRAM = """\
fn forgotten() i32:
    return Result.Ok(42)

fn main() i32:
    return Result.Ok(0)
"""


def _write(tmp_path: Path, name: str, source: str) -> Path:
    home = tmp_path / ONE_FIXTURE
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{name}.sushi"
    path.write_text(source, encoding="utf-8")
    return path


def _run(*flags: str, modules: list[Path] | None = None,
         programs: list[Path] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop(DOC_GATE_MODULES_ENV, None)
    env.pop(DEAD_GATE_PROGRAMS_ENV, None)
    if modules is not None:
        env[DOC_GATE_MODULES_ENV] = os.pathsep.join(str(m) for m in modules)
    if programs is not None:
        env[DEAD_GATE_PROGRAMS_ENV] = os.pathsep.join(str(p) for p in programs)
    return run_tests("--skip-build", *flags, env=env)


def test_a_dead_stdlib_declaration_and_import_fail_the_run(tmp_path):
    module = _write(tmp_path, "dead_probe_module", DEAD_MODULE)
    done = _run("--filter", ONE_FIXTURE, modules=[module], programs=[])
    out = done.stdout
    assert done.returncode == 1, out[-3000:]
    assert f"Passed: {FIXTURES_IN_IT}" in out, "the fixtures must pass:\n" + out[-3000:]
    assert "Stdlib doc-block gate: 1 module(s), 0 failed" in out, out[-3000:]
    assert "doc_gate/dead_probe_module: 2 finding(s)" in out, out[-3000:]
    assert "[CW1004]" in out and "'forgotten_answer'" in out, out[-3000:]
    assert "[CW3006]" in out and "'<time>'" in out, out[-3000:]
    assert "'live_answer'" not in out, out[-3000:]


def test_a_live_stdlib_module_passes(tmp_path):
    """The always-fires control for the red test above: a private name a public one uses."""
    module = _write(tmp_path, "live_probe_module", LIVE_MODULE)
    done = _run("--filter", ONE_FIXTURE, modules=[module], programs=[])
    assert done.returncode == 0, done.stdout[-3000:]
    assert "Dead-code gate: 1 module(s) and program(s), 0 failed" in done.stdout, (
        done.stdout[-3000:])


def test_a_dead_toolchain_declaration_fails_the_run(tmp_path):
    program = _write(tmp_path, "dead_probe_program", DEAD_PROGRAM)
    done = _run("--filter", ONE_FIXTURE, programs=[program])
    out = done.stdout
    assert done.returncode == 1, out[-3000:]
    assert "dead_probe_program.sushi: 1 finding(s)" in out, out[-3000:]
    assert "[CW1004]" in out and "'forgotten'" in out, out[-3000:]


def test_a_selection_with_no_module_or_program_prints_the_skip():
    """The real registry and the real toolchain: a one-fixture filter names neither."""
    done = _run("--filter", ONE_FIXTURE)
    assert done.returncode == 0, done.stdout[-3000:]
    assert (f"Dead-code gate SKIPPED: --filter {ONE_FIXTURE!r} selects no stdlib module "
            "or toolchain program.") in done.stdout, done.stdout[-3000:]


def test_the_gate_reads_the_toolchain_programs():
    """No second program list: without the override the gate compiles every
    `toolchain/src/` file that declares a main."""
    saved = os.environ.pop(DEAD_GATE_PROGRAMS_ENV, None)
    try:
        programs = dead_gate_programs(PROJECT_ROOT)
    finally:
        if saved is not None:
            os.environ[DEAD_GATE_PROGRAMS_ENV] = saved
    assert PROJECT_ROOT / "toolchain" / "src" / "slib_info.sushi" in programs
