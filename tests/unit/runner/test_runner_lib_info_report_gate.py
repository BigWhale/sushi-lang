"""The runner's library-report gate (#966): a runner step, so these tests start the
runner, the one exception to the rule that pytest never runs the Sushi compiler.

A fixture cannot use `--lib-info`. The gate builds one library in each kind, looks for
whole lines in the report of both halves (the `slib-info` tool it builds, and the Python
fallback), and runs a consumer that binds both payloads of a variant.
"""
from __future__ import annotations

import stat

from _harness import PROJECT_ROOT
from run_tests import (
    LIB_READER_TOOL_ENV, REPORT_KINDS, REPORT_LINES, lib_info_report_gate,
)

SILENT_TOOL = "#!/bin/sh\necho 'Library: whatever'\nexit 0\n"


def test_the_gate_fails_a_tool_that_prints_no_report(monkeypatch, tmp_path):
    """The always-fires control: every line fails the tool half, and only it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "slib-info"
    tool.write_text(SILENT_TOOL, encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv(LIB_READER_TOOL_ENV, str(bin_dir))
    result = lib_info_report_gate(PROJECT_ROOT)
    assert len(result.failures) == len(REPORT_KINDS) * len(REPORT_LINES), result.failures
    assert all("--lib-info (tool)" in f for f in result.failures), result.failures


def test_the_selection():
    assert lib_info_report_gate(PROJECT_ROOT, leaks_only=True).skip_reason == (
        "--leaks-only selects no library-reader check")
    skipped = lib_info_report_gate(PROJECT_ROOT, filter_pattern="diagnostics/borrow_help/")
    assert skipped.skip_reason == (
        "--filter 'diagnostics/borrow_help/' selects no library-reader check")
    assert skipped.report() == [
        "Library-report gate SKIPPED: --filter 'diagnostics/borrow_help/' selects no "
        "library-reader check."]
