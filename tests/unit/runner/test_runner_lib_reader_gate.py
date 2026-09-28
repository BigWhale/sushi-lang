"""The runner's library-reader gate (#977, #978, #967): a runner step, so these tests start
the runner, the one exception to the rule that pytest never runs the Sushi compiler.

A fixture cannot use `--lib-info`, and a damaged binary library cannot be a helper. The
gate builds a library, damages copies of it, and runs `--lib-info` in both halves (the
`slib-info` tool it builds, and the Python fallback) and a consumer over each copy.
`SUSHI_LIB_READER_GATE_TOOL_BIN` puts a tool of the test's own in place of the built one.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from _harness import PROJECT_ROOT, run_tests
from run_tests import (
    LIB_INFO_CASES, LIB_READER_GATE_PATH, LIB_READER_TOOL_ENV,
    lib_reader_gate,
)

SILENT_TOOL = "#!/bin/sh\necho 'Library: whatever'\nexit 0\n"


def _silent_tool(tmp_path: Path) -> Path:
    """A `slib-info` that reports every file as a whole library and prints no code."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "slib-info"
    tool.write_text(SILENT_TOOL, encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def test_the_gate_fails_a_tool_that_prints_no_code(monkeypatch, tmp_path):
    """The always-fires control: every damaged file fails the tool half, and only it."""
    monkeypatch.setenv(LIB_READER_TOOL_ENV, str(_silent_tool(tmp_path)))
    result = lib_reader_gate(PROJECT_ROOT)
    faults = [case for case, code, _make in LIB_INFO_CASES if code is not None]
    assert len(result.failures) == len(faults), "\n".join(result.failures)
    for case in faults:
        assert f"--lib-info (tool), {case}: exit 0" in "\n".join(result.failures), case
    assert not any("(python)" in f for f in result.failures), result.failures


def test_the_selection():
    assert lib_reader_gate(PROJECT_ROOT, leaks_only=True).skip_reason == (
        "--leaks-only selects no library-reader check")
    skipped = lib_reader_gate(PROJECT_ROOT, filter_pattern="diagnostics/borrow_help/")
    assert skipped.skip_reason == (
        "--filter 'diagnostics/borrow_help/' selects no library-reader check")
    assert "stdlib/slib/" in LIB_READER_GATE_PATH


def test_a_failed_gate_fails_the_run(tmp_path):
    env = dict(os.environ)
    env[LIB_READER_TOOL_ENV] = str(_silent_tool(tmp_path))
    done = run_tests("--skip-build", "--filter", LIB_READER_GATE_PATH, env=env)
    assert done.returncode == 1, done.stdout[-3000:]
    assert "Failed: 0" in done.stdout, "the fixtures must pass:\n" + done.stdout[-3000:]
    assert "Library-reader gate:" in done.stdout and "(tool)" in done.stdout, (
        done.stdout[-3000:])
