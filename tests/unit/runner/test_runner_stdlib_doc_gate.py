"""The runner's stdlib doc-block gate (#953, ruling 3): a runner step, so these tests start
the runner, the one exception to the rule that pytest never runs the Sushi compiler.

The gate compiles ONE program that imports every module, with `--warn-missing-docs` and
SUSHI_STDLIB_DOC_GATE set, and fails the run on a doc-pass diagnostic located in a module.
`SUSHI_DOC_GATE_MODULES` points it at a synthetic module, which the runner adds to the
registry of that one compiler process. A `--filter` selects a module by a substring of its
path, so each synthetic module sits under a directory that the filter of a one-fixture
selection also names.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from _harness import PROJECT_ROOT, run_tests
from run_tests import (
    DOC_GATE_MODULES_ENV, _doc_gate_selection, doc_gate_modules, doc_gate_program,
)

ONE_FIXTURE = "diagnostics/borrow_help/"

UNDOCUMENTED = """\
##: A synthetic module for the doc-block gate. :##

public fn undocumented_answer() i32:
    return 42
"""

DOCUMENTED = """\
##: A synthetic module for the doc-block gate. :##

##:
Gives the answer to life, the universe and everything.

- Returns: The answer.
:##
public fn documented_answer() i32:
    return 42
"""


def _module(tmp_path: Path, name: str, source: str) -> Path:
    home = tmp_path / ONE_FIXTURE
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{name}.sushi"
    path.write_text(source, encoding="utf-8")
    return path


def _run(*flags: str, modules: list[Path] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop(DOC_GATE_MODULES_ENV, None)
    if modules is not None:
        env[DOC_GATE_MODULES_ENV] = os.pathsep.join(str(m) for m in modules)
    return run_tests("--skip-build", *flags, env=env)


def test_an_undocumented_public_function_fails_the_run(tmp_path):
    module = _module(tmp_path, "gate_probe_undocumented", UNDOCUMENTED)
    done = _run("--filter", ONE_FIXTURE, modules=[module])
    assert done.returncode == 1, done.stdout[-3000:]
    assert "Passed: 1" in done.stdout, "the fixture itself must pass:\n" + done.stdout[-3000:]
    assert "doc_gate/gate_probe_undocumented: 1 finding(s)" in done.stdout, done.stdout[-3000:]
    assert "[CW7002]" in done.stdout and "'undocumented_answer'" in done.stdout, (
        done.stdout[-3000:])
    assert "doc-block gate failed" in done.stdout, done.stdout[-3000:]


def test_a_documented_module_passes(tmp_path):
    """The always-fires control for the red test above."""
    module = _module(tmp_path, "gate_probe_documented", DOCUMENTED)
    done = _run("--filter", ONE_FIXTURE, modules=[module])
    assert done.returncode == 0, done.stdout[-3000:]
    assert "Stdlib doc-block gate: 1 module(s), 0 failed" in done.stdout, done.stdout[-3000:]


def test_the_red_gate_is_in_the_json_report(tmp_path):
    module = _module(tmp_path, "gate_probe_json", UNDOCUMENTED)
    done = _run("--json", "--filter", ONE_FIXTURE, modules=[module])
    assert done.returncode == 1, done.stdout[-3000:]
    gate = json.loads(done.stdout)["stdlib_doc_gate"]
    assert gate["ran"] is True
    assert list(gate["failures"]) == ["doc_gate/gate_probe_json"], gate
    assert any("[CW7002]" in w for w in gate["failures"]["doc_gate/gate_probe_json"]), gate


def test_a_selection_with_no_stdlib_module_prints_the_skip():
    """The real registry: a one-fixture filter names no stdlib module."""
    done = _run("--filter", ONE_FIXTURE)
    assert done.returncode == 0, done.stdout[-3000:]
    assert (f"Stdlib doc-block gate SKIPPED: --filter {ONE_FIXTURE!r} selects no stdlib "
            "module.") in done.stdout, done.stdout[-3000:]


def test_leaks_only_prints_the_skip(tmp_path):
    """The module matches the filter, but `--leaks-only` selects no library build."""
    module = _module(tmp_path, "gate_probe_leaks", UNDOCUMENTED)
    done = _run("--leaks-only", "--filter", ONE_FIXTURE, modules=[module])
    assert "Stdlib doc-block gate SKIPPED: --leaks-only selects no stdlib module." in (
        done.stdout), done.stdout[-3000:]


def test_the_gate_reads_the_registry():
    """No second module list: without the override the gate builds SOURCE_STDLIB_MODULES."""
    from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
    saved = os.environ.pop(DOC_GATE_MODULES_ENV, None)
    try:
        assert doc_gate_modules() == (dict(SOURCE_STDLIB_MODULES), False)
    finally:
        if saved is not None:
            os.environ[DOC_GATE_MODULES_ENV] = saved


def test_a_whole_run_selects_every_module_and_a_filter_selects_by_path():
    """`--compile-only` does not narrow the gate: only a filter and `--leaks-only` do."""
    from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
    every = dict(SOURCE_STDLIB_MODULES)
    assert _doc_gate_selection(every, PROJECT_ROOT, None, False) == (every, None)
    chosen, skip = _doc_gate_selection(every, PROJECT_ROOT, "stdlib/", False)
    assert chosen == every and skip is None
    chosen, skip = _doc_gate_selection(every, PROJECT_ROOT, "net/", False)
    assert skip is None and sorted(chosen) == sorted(k for k in every if k.startswith("net/"))


def test_the_gate_program_imports_every_module_once():
    from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES
    program = doc_gate_program(dict(SOURCE_STDLIB_MODULES))
    for name in SOURCE_STDLIB_MODULES:
        assert program.count(f"use <{name}> as ") == 1, name
