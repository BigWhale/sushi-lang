"""The leak gate's double-free detector, in both directions (issue #359).

Every claim the gate makes rests on this detector, and it produced false FAILURES -- the
direction that costs the most time, because a newly red leak check reads as "this commit
caused it". These tests are C, not Sushi, because the mechanism is about WHO calls the
allocator, which a Sushi program cannot state.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "leakcheck"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from run_tests import build_leakcheck, leakcheck_lib_path  # noqa: E402

IS_DARWIN = sys.platform == "darwin"
DOUBLE_FREE_LINE = "SUSHI_LEAKCHECK: DOUBLE_FREE"


@pytest.fixture(scope="module")
def interposer() -> Path:
    """The malloc interposer, built the same way the harness builds it."""
    if not build_leakcheck(PROJECT_ROOT):
        pytest.skip("could not build the leak interposer")
    shim = leakcheck_lib_path(PROJECT_ROOT)
    if not shim.exists():
        pytest.skip("leak interposer not built")
    return shim


@pytest.fixture(scope="module")
def alien_library(tmp_path_factory) -> Path:
    """A shared library whose call sites sit outside the main executable's text."""
    out = tmp_path_factory.mktemp("alien") / ("libalien.dylib" if IS_DARWIN else "libalien.so")
    src = FIXTURES / "alien_library.c"
    cmd = ([("cc"), "-dynamiclib", "-O0", "-install_name", str(out), "-o", str(out), str(src)]
           if IS_DARWIN else
           ["cc", "-shared", "-fPIC", "-O0", "-o", str(out), str(src)])
    _compile(cmd)
    return out


def _compile(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.skip(f"cc failed: {' '.join(cmd)}\n{result.stderr}")


def _build(name: str, tmp_path: Path, alien: Path | None) -> Path:
    """Compile one fixture at -O0: at -O1 clang folds a malloc/free pair away."""
    out = tmp_path / name
    cmd = ["cc", "-O0", "-o", str(out), str(FIXTURES / f"{name}.c")]
    if alien is not None:
        cmd += [str(alien)]
        if not IS_DARWIN:
            cmd += [f"-Wl,-rpath,{alien.parent}"]
    _compile(cmd)
    return out


def _run_under_interposer(binary: Path, shim: Path) -> subprocess.CompletedProcess:
    key = "DYLD_INSERT_LIBRARIES" if IS_DARWIN else "LD_PRELOAD"
    import os
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=120,
                          env={**os.environ, key: str(shim)})


def test_a_reused_address_is_not_a_double_free(interposer, alien_library, tmp_path):
    binary = _build("reused_address", tmp_path, alien_library)
    proc = _run_under_interposer(binary, interposer)

    if "reused=1" not in proc.stdout:
        pytest.skip("the allocator did not hand the freed address back; nothing to assert")
    assert DOUBLE_FREE_LINE not in proc.stderr, (
        "the gate reported a double free that no code committed: the program freed an "
        "address, a library then allocated the same address and freed its OWN block, and "
        "the retained key matched.\n" + proc.stderr
    )
    assert "double_frees=0" in proc.stderr, proc.stderr


def test_a_real_double_free_is_still_reported(interposer, tmp_path):
    binary = _build("real_double_free", tmp_path, None)
    proc = _run_under_interposer(binary, interposer)

    assert DOUBLE_FREE_LINE in proc.stderr, (
        "the gate no longer catches a program that frees the same pointer twice; the fix "
        "for the false positive went too far.\n" + proc.stderr
    )
    assert "double_frees=1" in proc.stderr, proc.stderr


def test_a_library_freeing_our_block_is_still_reported(interposer, alien_library, tmp_path):
    """The reason the fix keys on address reuse, not on who calls `free`.

    Suppressing every report whose caller sits outside the main executable would lose this
    one: the block is ours, the library never allocated it, and the second free is real.
    """
    binary = _build("alien_frees_our_block", tmp_path, alien_library)
    proc = _run_under_interposer(binary, interposer)

    assert DOUBLE_FREE_LINE in proc.stderr, (
        "a library freeing a block the program had already freed went unreported; that is "
        "a genuine double free, whatever the call site.\n" + proc.stderr
    )
    assert "double_frees=1" in proc.stderr, proc.stderr
