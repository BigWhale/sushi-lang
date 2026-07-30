"""Wiring tests for the leak gate (issue #241).

`EXPECT_NO_LEAKS` is an assertion, not a hint: a test that carries it must be
leak-checked by every run that executes it. Before #241 the assertion was gated on a
separate `--leaks` flag, so a plain `--enhanced` run skipped all 96 of them without
saying so -- no check, no skip notice, nothing in the summary. A leaking program
sailed through the full suite.

These tests pin the wiring rather than the checker itself (the checker is
tests/leakcheck/leakcheck.c, exercised by the .sushi corpus):

* a genuinely leaking program annotated `EXPECT_NO_LEAKS` fails plain `--enhanced`
* `_check_leaks` is reached with no leak flag anywhere
* `--leaks` is gone -- there is one enforcing mode, not two
* a skipped check is recorded against the TEST name, so the notice is actionable
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
LEAKING_FIXTURE = FIXTURES_DIR / "leaking_program.sushi"

# tests/ is not a package; the harness modules import each other flat.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
from test_metadata import parse_test_metadata, should_run_runtime_test  # noqa: E402


def _run_harness(*args: str) -> subprocess.CompletedProcess:
    """Invoke tests/run_tests.py the way a developer or CI does."""
    return subprocess.run(
        [sys.executable, str(TESTS_DIR / "run_tests.py"), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )


@pytest.fixture
def leaking_test_in_suite():
    """Drop the leaking fixture into the suite's discovery glob, under a unique dir.

    The fixture deliberately does not live under tests/**/test_*.sushi -- the runner
    would collect it and the suite would be permanently red. The runner only scans
    tests/, so exercising it end to end means planting a copy there for the duration
    of one assertion and taking it away again.
    """
    marker = f"leakgate_tmp_{os.getpid()}"
    staging = TESTS_DIR / marker
    staging.mkdir()
    try:
        shutil.copy(LEAKING_FIXTURE, staging / "test_leakgate_deliberate_leak.sushi")
        yield marker
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_leaking_program_fails_plain_enhanced(leaking_test_in_suite):
    """A leaking `EXPECT_NO_LEAKS` test must fail `--enhanced` with no extra flags.

    This is the whole issue in one assertion. The fixture leaks 4096 bytes through a
    raw libc malloc, so the interposer has something unambiguous to find; if the run
    is green, the check did not happen.
    """
    proc = _run_harness("--enhanced", "--filter", leaking_test_in_suite)
    output = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        "a deliberately leaking EXPECT_NO_LEAKS test passed --enhanced; "
        f"the leak assertion did not run\n{output}"
    )
    assert "Leak check: leaked" in output, (
        f"expected a leak-check failure in the output, got:\n{output}"
    )


def test_enhanced_builds_the_interposer(leaking_test_in_suite, tmp_path):
    """`--enhanced` builds the interposer, so a fresh clone checks rather than skips.

    The built library is gitignored. If only `--leaks-only` builds it, every
    `--enhanced` run on a fresh checkout degrades to 96 "interposer not built" skips,
    which is the silent pass this issue is about wearing a different hat.
    """
    shim = enhanced_test_runner.leakcheck_lib_path(PROJECT_ROOT)
    stashed = None
    if shim.exists():
        stashed = tmp_path / shim.name
        shutil.move(str(shim), stashed)
    try:
        proc = _run_harness("--enhanced", "--filter", leaking_test_in_suite)
        assert shim.exists(), (
            "--enhanced did not build the leak interposer\n"
            + proc.stdout + proc.stderr
        )
    finally:
        if not shim.exists() and stashed is not None:
            shutil.move(str(stashed), str(shim))


def test_check_leaks_runs_without_any_leak_flag(tmp_path, monkeypatch):
    """`run_single_test` reaches `_check_leaks` for a leak-annotated test by default.

    Nothing about the runner's construction should decide whether an assertion the
    test file declares is honoured.
    """
    source = tmp_path / "test_leak_annotated.sushi"
    source.write_text(
        "# EXPECT_NO_LEAKS: true\n"
        '# EXPECT_STDOUT_CONTAINS: "Mostly Harmless"\n'
        "\n"
        "fn main() i32:\n"
        '    println("Mostly Harmless")\n'
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )

    seen: list[str] = []

    def _record(self, test_name, binary_path, metadata):
        seen.append(test_name)
        return True, "checked"

    monkeypatch.setattr(enhanced_test_runner.TestRunner, "_check_leaks", _record)

    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        result = runner.run_single_test(source)

    assert result.total_success, result.compilation_message
    assert seen == ["test_leak_annotated.sushi"], (
        f"_check_leaks was not invoked for a leak-annotated test; calls: {seen}"
    )


def test_leaks_flag_no_longer_exists():
    """`--leaks` is gone: `--enhanced` enforces, `--leaks-only` selects.

    Keeping it as a no-op alias would preserve the belief that enforcement is
    something you opt into.
    """
    proc = _run_harness("--leaks", "--filter", "no_such_test_pattern")
    assert proc.returncode != 0, "--leaks was accepted; it should be an argparse error"
    assert "unrecognized arguments: --leaks" in proc.stderr, proc.stderr


def test_warning_test_with_leak_assertion_is_executed():
    """A `test_warn_*` test declaring `EXPECT_NO_LEAKS` is run, unconditionally.

    Shadowing an owning binding is warned-but-legal, so executing the binary is the
    only way to leak-check it. `should_run_runtime_test` used to gate that on a
    `leaks_mode` argument, which meant the two shadow tests were never executed by
    `--enhanced` at all.
    """
    warn_tests = sorted(TESTS_DIR.glob("memory/test_warn_shadow_owning_*.sushi"))
    assert warn_tests, "expected the shadow-owning warning tests to exist"

    for test_file in warn_tests:
        metadata = parse_test_metadata(test_file)
        assert metadata.expect_no_leaks, f"{test_file.name} lost its leak assertion"
        assert should_run_runtime_test(test_file, metadata), (
            f"{test_file.name} declares EXPECT_NO_LEAKS but is never executed"
        )


def test_skipped_leak_check_records_the_test_name(tmp_path, monkeypatch):
    """A skipped check is recorded as (test name, reason), not as a temp binary name.

    The recorded name used to be `test_<stem>_<pid>` -- the throwaway binary in the
    run's temp dir, which maps back to nothing. A skip notice you cannot act on is
    barely better than no notice.
    """
    missing = tmp_path / "leakcheck.dylib"
    monkeypatch.setattr(enhanced_test_runner, "leakcheck_lib_path", lambda root: missing)

    metadata = enhanced_test_runner.TestMetadata()
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        ok, message = runner._check_leaks(
            "test_something.sushi", tmp_path / "test_something_4242", metadata
        )

    assert ok is None, "a missing interposer must skip, never pass"
    assert runner.leaks_skipped == [("test_something.sushi", "interposer not built")], (
        f"skip was recorded as {runner.leaks_skipped}"
    )
    assert "skipped" in message.lower()
