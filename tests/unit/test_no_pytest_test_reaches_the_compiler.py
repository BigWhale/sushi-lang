"""The compiler-stage gate fires, and it fails the right items (#959).

pytest never runs the Sushi compiler; only a test of the test runner, under
`tests/unit/runner/`, may start it. `compiler_stage_gate` holds that rule, and this module
is its always-fires control: a gate that watched nothing would pass every run in silence.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import compiler_stage_gate
from compiler_stage_gate import compiler_argument

UNIT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = UNIT_ROOT.parents[1]

PROGRAM = "fn main() i32:\n    return Result.Ok(0)\n"


def _gate() -> compiler_stage_gate.CompilerStageGate:
    assert compiler_stage_gate.GATE is not None, "conftest.py did not install the gate"
    return compiler_stage_gate.GATE


def test_a_parse_outside_the_runner_set_is_caught(request):
    """The control: a real Sushi parse in this module is a hit against this test."""
    from sushi_lang.internals.parser import parse_to_ast

    with _gate().recorded_during(request.node) as probe:
        parse_to_ast(PROGRAM)
    assert "lark.Lark.parse" in probe.stages, probe.stages


def test_a_compiler_subprocess_is_caught(request):
    """A process whose arguments name a `.sushi` file is a compiler run, whatever it is."""
    with _gate().recorded_during(request.node) as probe:
        subprocess.run([sys.executable, "-c", "pass", "probe.sushi"], check=True)
    assert probe.stages == ["subprocess (probe.sushi)"], probe.stages


@pytest.mark.parametrize("owner, name", [
    ("lark:Lark", "parse"),
    ("lark:Lark", "lex"),
    ("sushi_lang.semantics.semantic_analyzer:SemanticAnalyzer", "check"),
    ("sushi_lang.backend.codegen_llvm:LLVMCodegen", "__init__"),
    ("sushi_lang.backend.driver:LLVMDriver", "__init__"),
    ("subprocess:Popen", "__init__"),
])
def test_every_stage_is_wrapped(owner, name):
    import importlib

    module, cls = owner.split(":")
    method = getattr(getattr(importlib.import_module(module), cls), name)
    assert getattr(method, "__wrapped__", None) is not None, f"{owner}.{name} is not watched"


@pytest.mark.parametrize("args, found", [
    (["/repo/sushic", "a.sushi", "-o", "a"], "/repo/sushic"),
    ("./sushic --version", "./sushic"),
    (["./sushic", "--version"], "./sushic"),
    ([sys.executable, "tests/run_tests.py", "--filter", "x"], "tests/run_tests.py"),
    ([sys.executable, "tests/enhanced_test_runner.py"], "tests/enhanced_test_runner.py"),
    ([sys.executable, "toolchain/build.py"], "toolchain/build.py"),
    ([sys.executable, "-m", "sushi_lang", "a"], "-m sushi_lang"),
    ([sys.executable, "-m", "sushi_lang.compiler"], "-m sushi_lang.compiler"),
    ([sys.executable, "-m", "sushi_lang.packager", "list"], None),
    ([sys.executable, "-m", "vulture", "sushi_lang"], None),
    (["grep", "-rn", "ERR.CE2510", "sushi_lang"], None),
    (["cc", "-shared", "probe.c"], None),
])
def test_what_names_a_compiler_run(args, found):
    assert compiler_argument(args) == found


INNER_TESTS = {
    "test_outside.py": f"""
from sushi_lang.internals.parser import parse_to_ast

def test_parses():
    parse_to_ast({PROGRAM!r})

def test_does_nothing():
    pass
""",
    "runner/test_inside.py": f"""
from sushi_lang.internals.parser import parse_to_ast

def test_parses_in_the_runner_set():
    parse_to_ast({PROGRAM!r})
""",
    "test_fixture.py": f"""
import pytest
from sushi_lang.internals.parser import parse_to_ast

@pytest.fixture(scope="module")
def parsed():
    return parse_to_ast({PROGRAM!r})

def test_first(parsed):
    pass

def test_second(parsed):
    pass

def test_without_it():
    pass
""",
    "test_at_import.py": f"""
from sushi_lang.internals.parser import parse_to_ast

TREE = parse_to_ast({PROGRAM!r})

def test_never_runs():
    pass
""",
}


@pytest.fixture(scope="module")
def inner_run(tmp_path_factory):
    """One pytest session over a copy of this layer's conftest and gate."""
    home = tmp_path_factory.mktemp("gate")
    for name in ("conftest.py", "compiler_stage_gate.py"):
        (home / name).write_text((UNIT_ROOT / name).read_text(encoding="utf-8"),
                                 encoding="utf-8")
    for name, body in INNER_TESTS.items():
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(body, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "--continue-on-collection-errors",
         "--rootdir", str(home), str(home)],
        cwd=home, capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)})
    return done


def _outcome(run: subprocess.CompletedProcess, test: str) -> str:
    for line in run.stdout.splitlines():
        if line.split(" ", 1)[-1].startswith(test) and line.split(" ", 1)[0] in (
                "PASSED", "FAILED", "ERROR"):
            return line.split(" ", 1)[0]
    raise AssertionError(f"no outcome for {test}:\n{run.stdout}")


def test_a_test_that_parses_outside_the_runner_set_fails(inner_run):
    assert _outcome(inner_run, "test_outside.py::test_parses") == "FAILED", inner_run.stdout
    assert "compiler-stage gate" in inner_run.stdout, inner_run.stdout


def test_a_test_that_parses_nothing_passes(inner_run):
    assert _outcome(inner_run, "test_outside.py::test_does_nothing") == "PASSED"


def test_the_runner_set_may_parse(inner_run):
    assert _outcome(inner_run,
                    "runner/test_inside.py::test_parses_in_the_runner_set") == "PASSED"


def test_a_fixture_that_parses_fails_every_test_that_uses_it(inner_run):
    # The hit lands on the SETUP report, so pytest prints ERROR; either word fails the run.
    assert _outcome(inner_run, "test_fixture.py::test_first") in ("FAILED", "ERROR")
    assert _outcome(inner_run, "test_fixture.py::test_second") in ("FAILED", "ERROR")
    assert _outcome(inner_run, "test_fixture.py::test_without_it") == "PASSED"
    assert "the fixture 'parsed' reached lark.Lark.parse" in inner_run.stdout


def test_a_module_that_parses_at_import_fails_its_collection(inner_run):
    assert "ERROR collecting test_at_import.py" in inner_run.stdout, inner_run.stdout
    assert inner_run.returncode != 0
