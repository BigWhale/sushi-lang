"""A leak assertion that was not evaluated must not report a pass (issue #605).

The runner counted a skipped leak check, printed it in yellow, and then left it out of
the exit code. An interposer that builds and does not LOAD -- a `leakcheck.so` for the
wrong architecture, which has happened -- turns every `EXPECT_NO_LEAKS` into a skip, and
the run still exited 0. `--leaks-only` is the worst version: it selects the annotated
subset, so the whole job can pass with nothing asserted.

The rule now: an unevaluated leak assertion fails the run. `--allow-leak-skips` is the
one escape, for a platform that genuinely cannot check.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent

# tests/ is not a package; the harness modules import each other flat.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
import run_tests  # noqa: E402

LEAK_ASSERTED = (
    "# EXPECT_NO_LEAKS: true\n"
    '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'
    "\n"
    "fn main() i32:\n"
    '    println("Mostly Harmless")\n'
    "    return Result.Ok(0)\n"
)

PLAIN = (
    '# EXPECT_STDOUT_EXACT: "Mostly Harmless\\n"\n'
    "\n"
    "fn main() i32:\n"
    '    println("Mostly Harmless")\n'
    "    return Result.Ok(0)\n"
)


@pytest.fixture
def staged(request):
    """One fixture inside the corpus, so `--filter` can select it and nothing else."""
    marker = f"leakskip_tmp_{request.node.name}"
    home = TESTS_DIR / marker
    home.mkdir()
    try:
        yield marker, home
    finally:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def no_interposer(monkeypatch, tmp_path):
    """The failure this closes: a shim the runner cannot use, so every check skips."""
    monkeypatch.setattr(enhanced_test_runner, "leakcheck_lib_path",
                        lambda root: tmp_path / "leakcheck-that-is-not-there.dylib")


def _run(monkeypatch, marker: str, *extra: str) -> int:
    monkeypatch.setattr(sys, "argv",
                        ["enhanced_test_runner.py", "--skip-build", "--filter", marker,
                         *extra])
    return enhanced_test_runner.main()


def test_a_skipped_leak_check_fails_the_run(staged, no_interposer, monkeypatch, capsys):
    marker, home = staged
    (home / "test_leakskip_asserted.sushi").write_text(LEAK_ASSERTED, encoding="utf-8")

    exit_code = _run(monkeypatch, marker)
    output = capsys.readouterr().out

    assert exit_code == 1, (
        "the run reported success with its only leak assertion unevaluated\n" + output)


def test_the_summary_does_not_call_that_run_a_pass(staged, no_interposer, monkeypatch,
                                                   capsys):
    marker, home = staged
    (home / "test_leakskip_summary.sushi").write_text(LEAK_ASSERTED, encoding="utf-8")

    _run(monkeypatch, marker)
    output = capsys.readouterr().out

    assert "All tests passed" not in output, (
        "the summary still announces a clean run\n" + output)
    assert "--allow-leak-skips" in output, (
        "the summary does not say how a deliberate skip is excused\n" + output)


def test_an_excused_skip_still_passes(staged, no_interposer, monkeypatch, capsys):
    """The escape, for a platform that cannot check at all."""
    marker, home = staged
    (home / "test_leakskip_excused.sushi").write_text(LEAK_ASSERTED, encoding="utf-8")

    exit_code = _run(monkeypatch, marker, "--allow-leak-skips")
    output = capsys.readouterr().out

    assert exit_code == 0, (
        "--allow-leak-skips did not excuse the skip it exists for\n" + output)


def test_a_run_with_no_leak_assertion_is_unaffected(staged, no_interposer, monkeypatch,
                                                    capsys):
    """Nothing was skipped, so nothing changes: the rule is about a skip, not a shim."""
    marker, home = staged
    (home / "test_leakskip_plain.sushi").write_text(PLAIN, encoding="utf-8")

    exit_code = _run(monkeypatch, marker)
    output = capsys.readouterr().out

    assert exit_code == 0, (
        "a run that asserts no leaks anywhere was failed by a missing shim\n" + output)


def test_the_flag_reaches_the_enhanced_runner(staged, monkeypatch):
    """`run_tests.py` rebuilds argv by hand, so the flag has to be forwarded there."""
    marker, home = staged
    (home / "test_leakskip_forward.sushi").write_text(PLAIN, encoding="utf-8")

    forwarded: list[list[str]] = []

    def _capture() -> int:
        forwarded.append(list(sys.argv))
        return 0

    monkeypatch.setattr(enhanced_test_runner, "main", _capture)
    monkeypatch.setattr(sys, "argv",
                        ["run_tests.py", "--enhanced", "--allow-leak-skips",
                         "--skip-build", "--filter", marker])
    assert run_tests.main() == 0

    assert forwarded, "run_tests.py never reached the enhanced runner"
    assert "--allow-leak-skips" in forwarded[0], (
        f"the flag was dropped on the way through: {forwarded[0]}")


def test_the_leaks_only_selector_is_covered_by_the_same_rule(staged, no_interposer,
                                                             monkeypatch, capsys):
    """`--leaks-only` runs only annotated tests, so a skip there asserts nothing."""
    marker, home = staged
    (home / "test_leakskip_only.sushi").write_text(LEAK_ASSERTED, encoding="utf-8")

    exit_code = _run(monkeypatch, marker, "--leaks-only")
    output = capsys.readouterr().out

    assert exit_code == 1, (
        "--leaks-only passed with every assertion it selected unevaluated\n" + output)
