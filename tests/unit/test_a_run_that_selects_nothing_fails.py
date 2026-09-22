"""A run that selected no fixture reports a failure, and there is ONE runner to report it.

Two faults, one shape. `tests/run_tests.py` carried a second, weaker runner that checked the
compiler's exit status and read no `EXPECT_*` directive at all, so a `test_err_` fixture
passed it whatever diagnostic the compiler printed (#760). And the enhanced runner answered
an empty result dict for an empty selection, which `main` read as "nothing failed" and
reported as exit 0 (#765).

Both say the same thing to a reader: a run that asserted nothing looks exactly like a run
that passed. The measurement that settled it -- the weaker runner was 6.95s against the
enhanced runner's 6.85s over `tests/diagnostics/`, where almost nothing runs a binary -- is
in the changelog: it bought no speed and gave up the assertions.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TESTS_DIR.parent
RUN_TESTS = TESTS_DIR / "run_tests.py"
ENHANCED = TESTS_DIR / "enhanced_test_runner.py"


def _run(*flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUN_TESTS), "--skip-build", *flags],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300,
    )


def test_an_empty_selection_fails():
    """#765: the mode CI trusts must not answer 0 for a run that covered nothing."""
    done = _run("--filter", "zzz_no_such_area")
    assert done.returncode != 0, (
        "a run that selected no fixture reported success:\n" + done.stdout[-2000:])


def test_an_empty_selection_fails_under_the_enhanced_spelling_too():
    """The exact shape of #765.

    The two runners used to disagree about an empty selection: the weaker one answered 1
    and the enhanced one answered 0, so the mode CI and the wave protocol trust was the
    one that reported a pass for a run that covered nothing.
    """
    done = _run("--enhanced", "--filter", "zzz_no_such_area")
    assert done.returncode != 0, (
        "the enhanced spelling reported success for an empty selection:\n"
        + done.stdout[-2000:])


def test_an_empty_selection_says_which_selection_was_empty():
    done = _run("--filter", "zzz_no_such_area")
    assert "zzz_no_such_area" in done.stdout + done.stderr, done.stdout[-2000:]


def test_an_empty_selection_fails_the_same_way_behind_every_flag():
    """The flags compose, so an empty selection has more than one way to happen."""
    done = _run("--leaks-only", "--compile-only")
    assert done.returncode != 0, (
        "a leak assertion needs a run, so these two select nothing; the run reported "
        "success:\n" + done.stdout[-2000:])


def test_an_empty_selection_is_still_a_json_report():
    """A consumer reads the report, not the exit code, so the refusal must BE a report."""
    done = _run("--json", "--filter", "zzz_no_such_area")
    assert done.returncode != 0, done.stdout[-2000:]
    report = json.loads(done.stdout)
    assert report["selected_nothing"] is True, report
    assert report["passed"] == 0 and report["total_tests"] == 0, report


def test_a_real_selection_still_passes():
    """The always-fires control: the refusal above must not refuse a genuine run."""
    done = _run("--filter", "diagnostics/borrow_help/")
    assert done.returncode == 0, done.stdout[-3000:]
    assert "Passed: 1" in done.stdout, done.stdout[-3000:]


def test_there_is_one_runner():
    """#760: no second, weaker per-test loop may come back.

    `run_tests.py` keeps the shared build and pre-flight helpers the enhanced runner
    imports from it. What it must not keep is a per-test loop of its own, because a second
    loop is a second answer to "did this fixture pass".
    """
    tree = ast.parse(RUN_TESTS.read_text(encoding="utf-8"), filename=str(RUN_TESTS))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "run_single_test" not in names, (
        "run_tests.py grew a per-test loop again; the one runner is "
        "enhanced_test_runner.py")


def test_the_front_end_always_reaches_the_one_runner():
    source = RUN_TESTS.read_text(encoding="utf-8")
    assert "enhanced_test_runner" in source, "the front end no longer names the runner"


def test_enhanced_is_accepted_and_changes_nothing():
    """The flag stays for the CI lines and for muscle memory, and is a no-op."""
    plain = _run("--filter", "diagnostics/borrow_help/")
    flagged = _run("--enhanced", "--filter", "diagnostics/borrow_help/")
    assert plain.returncode == flagged.returncode == 0
    assert "Passed: 1" in flagged.stdout, flagged.stdout[-3000:]


def test_compile_only_runs_no_binary():
    """The selector's whole promise: nothing it selects needs the binary."""
    done = _run("--compile-only", "--filter", "enums/")
    assert done.returncode == 0, done.stdout[-3000:]
    assert "Passed:" in done.stdout, done.stdout[-3000:]


def test_the_empty_refusal_is_in_the_runner_not_the_front_end():
    """One home for the rule, so a future entry point cannot bypass it."""
    source = ENHANCED.read_text(encoding="utf-8")
    assert "select_fixtures" in source, (
        "the enhanced runner no longer asks the one selector")
