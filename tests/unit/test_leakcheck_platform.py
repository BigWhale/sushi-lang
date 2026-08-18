"""Which platforms the leak gate supports, and what it says about the rest (issue #275).

`leakcheck_lib_path` mapped every non-Darwin platform to `linux`, so on anything else it
named a path that could not exist and the run reported "interposer not built" -- a reason
that is not the real one. Since #241 the leak assertion is enforced on every `--enhanced`
run, so the wrong reason is what a Windows user would see by default.

Two platforms are supported, macOS and Linux. Anything else is declined explicitly and
says so; there is no fallback and no guess.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = TESTS_DIR.parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402
import run_tests  # noqa: E402

SUPPORTED = {
    "darwin": ("darwin", "leakcheck.dylib"),
    "linux": ("linux", "leakcheck.so"),
    "linux2": ("linux", "leakcheck.so"),
}
UNSUPPORTED = ["win32", "cygwin", "freebsd13", "openbsd7", "sunos5", "emscripten"]


@pytest.mark.parametrize("platform", sorted(SUPPORTED))
def test_a_supported_platform_names_its_own_interposer(monkeypatch, platform):
    key, filename = SUPPORTED[platform]
    monkeypatch.setattr(sys, "platform", platform)

    assert run_tests.leakcheck_platform() == key
    path = run_tests.leakcheck_lib_path(PROJECT_ROOT)
    assert path is not None
    assert path.parent.name == key
    assert path.name == filename


@pytest.mark.parametrize("platform", UNSUPPORTED)
def test_an_unsupported_platform_is_declined_rather_than_guessed(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)

    assert run_tests.leakcheck_platform() is None, (
        f"{platform} was mapped to a platform key, so the harness would look for an "
        f"interposer that cannot exist there"
    )
    assert run_tests.leakcheck_lib_path(PROJECT_ROOT) is None


@pytest.mark.parametrize("platform", UNSUPPORTED)
def test_the_build_declines_instead_of_running_a_linux_command(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)

    def no_subprocess(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("build_leakcheck invoked the compiler on an unsupported platform")

    monkeypatch.setattr(run_tests.subprocess, "run", no_subprocess)
    assert run_tests.build_leakcheck(PROJECT_ROOT) is False


def test_the_skip_reason_names_the_platform_not_a_missing_build(tmp_path, monkeypatch):
    """The point of the issue: the recorded reason must be the real one."""
    monkeypatch.setattr(enhanced_test_runner, "leakcheck_lib_path", lambda root: None)

    metadata = enhanced_test_runner.TestMetadata()
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        ok, message = runner._check_leaks(
            "test_something.sushi", tmp_path / "test_something_4242", metadata
        )

    assert ok is None, "an unsupported platform must skip, never pass"
    assert runner.leaks_skipped == [
        ("test_something.sushi", enhanced_test_runner.SKIP_UNSUPPORTED_PLATFORM)
    ], f"skip was recorded as {runner.leaks_skipped}"
    assert enhanced_test_runner.SKIP_UNSUPPORTED_PLATFORM != enhanced_test_runner.SKIP_NOT_BUILT
    assert "skipped" in message.lower()
