"""Wiring tests for the descriptor gate (HANDLES.md, Phase 2).

`EXPECT_NO_LEAKS` counts BYTES. It cannot see a file or a socket, so a handle test
gets no coverage from it -- a program that opens a descriptor and never closes it
reports a perfectly clean byte balance. `EXPECT_NO_OPEN_FDS` is the half that sees it.

Constraint 5 of `docs/design/ir.md` section 11 is why it is built now rather than
later: "Extend EXPECT_NO_LEAKS coverage BEFORE lowering the owning types, not during.
A missing leak test there is a bug that survives to the end undetected."
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
FD_FIXTURE = FIXTURES_DIR / "fd_leaking_program.sushi"

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
def fd_leaking_test_in_suite():
    """Drop the descriptor fixture into the suite's discovery glob, under a unique dir."""
    marker = f"fdgate_tmp_{os.getpid()}"
    staging = TESTS_DIR / marker
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / "test_fd_gate_fixture.sushi"
    shutil.copyfile(FD_FIXTURE, staged)
    try:
        yield marker, staged
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def test_the_directive_parses():
    """A bare and a valued form both set the flag, matching EXPECT_NO_LEAKS."""
    metadata = parse_test_metadata(FD_FIXTURE)
    assert metadata.expect_no_open_fds is True


def test_a_warning_test_with_only_a_descriptor_assertion_is_executed():
    """The directive must make a test_warn_ program RUN, or the assertion is inert."""
    from test_metadata import TestMetadata

    metadata = TestMetadata()
    metadata.expect_no_open_fds = True
    assert should_run_runtime_test(Path("test_warn_thing.sushi"), metadata) is True


def test_a_leaked_descriptor_fails_the_gate(fd_leaking_test_in_suite):
    """The whole point: a program the BYTE gate calls clean must fail this one."""
    marker, _staged = fd_leaking_test_in_suite
    result = _run_harness("--enhanced", "--filter", marker)
    assert result.returncode != 0, (
        "a program that opens a file and never closes it passed the suite. "
        f"stdout:\n{result.stdout}"
    )
    assert "descriptor" in result.stdout.lower(), (
        f"the failure did not name the descriptor check:\n{result.stdout}"
    )


def test_the_byte_gate_calls_the_same_program_clean(fd_leaking_test_in_suite):
    """The two gates count different things, and this fixture proves it.

    If this ever fails, the fixture started leaking memory too and stopped being a
    clean demonstration that EXPECT_NO_LEAKS cannot see a descriptor.
    """
    import re

    from run_tests import build_leakcheck

    marker, staged = fd_leaking_test_in_suite
    assert build_leakcheck(PROJECT_ROOT), "the interposer must build to run this"
    shim = enhanced_test_runner.leakcheck_lib_path(PROJECT_ROOT)
    if shim is None or not shim.exists():
        pytest.skip("leak checking is not supported here")

    binary = staged.parent / "fd_fixture_bin"
    compiled = subprocess.run(
        [str(PROJECT_ROOT / "sushic"), str(staged), "-o", str(binary)],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
    assert compiled.returncode in (0, 1), compiled.stderr

    preload = ({"DYLD_INSERT_LIBRARIES": str(shim)} if sys.platform == "darwin"
               else {"LD_PRELOAD": str(shim)})
    run = subprocess.run([str(binary)], cwd=staged.parent, capture_output=True,
                         text=True, timeout=60, env={**os.environ, **preload})

    report = re.search(r"SUSHI_LEAKCHECK: leaked=(\d+) .*open_fds=(-?\d+)", run.stderr)
    assert report is not None, f"no interposer report:\n{run.stderr}"
    assert int(report.group(1)) == 0, "the fixture must leak no BYTES, only a descriptor"
    assert int(report.group(2)) == 1, "the fixture must leak exactly one descriptor"
