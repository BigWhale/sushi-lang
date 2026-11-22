"""The runner's library-report gate (#966): a runner step, so these tests start the
runner, the one exception to the rule that pytest never runs the Sushi compiler.

A fixture cannot use `--lib-info`. The gate builds one library, looks for whole lines in
the report of both halves (the `slib-info` tool it builds, and the Python fallback), looks
for no line that starts with a prefix of `REPORT_ABSENT`, and runs a consumer that binds
both payloads of a variant.
"""
from __future__ import annotations

import stat

from _harness import PROJECT_ROOT
from run_tests import (
    LIB_READER_TOOL_ENV, REPORT_ABSENT, REPORT_EXTRA_CASES, REPORT_LINES,
    lib_info_report_gate,
)

SILENT_TOOL = "#!/bin/sh\necho 'Library: whatever'\nexit 0\n"
# A tool that prints a whole report from the Python half, and then one line of each
# prefix that no report may print.
STRAY_TOOL = ("#!/bin/sh\nSUSHI_TOOLCHAIN=off {sushic} --lib-info \"$1\"\n"
              + "".join(f"echo '{prefix} x'\n" for prefix in REPORT_ABSENT))


def _tool(tmp_path, text: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "slib-info"
    tool.write_text(text, encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def test_the_gate_fails_a_tool_that_prints_no_report(monkeypatch, tmp_path):
    """The always-fires control: every line and every extra case fails the tool half, and
    only it."""
    monkeypatch.setenv(LIB_READER_TOOL_ENV, str(_tool(tmp_path, SILENT_TOOL)))
    result = lib_info_report_gate(PROJECT_ROOT)
    expected = len(REPORT_LINES) + len(REPORT_EXTRA_CASES)
    assert len(result.failures) == expected, result.failures
    assert all("--lib-info (tool)" in f for f in result.failures), result.failures


def test_the_gate_fails_a_tool_that_prints_a_line_of_a_removed_field(monkeypatch, tmp_path):
    """Each prefix of `REPORT_ABSENT` fails the tool half one time, and only it."""
    sushic = PROJECT_ROOT / "sushic"
    monkeypatch.setenv(LIB_READER_TOOL_ENV,
                       str(_tool(tmp_path, STRAY_TOOL.format(sushic=sushic))))
    result = lib_info_report_gate(PROJECT_ROOT)
    assert len(result.failures) == len(REPORT_ABSENT), result.failures
    for prefix in REPORT_ABSENT:
        assert f"--lib-info (tool): printed '{prefix} x'" in result.failures, result.failures


def test_the_selection():
    assert lib_info_report_gate(PROJECT_ROOT, leaks_only=True).skip_reason == (
        "--leaks-only selects no library-reader check")
    skipped = lib_info_report_gate(PROJECT_ROOT, filter_pattern="diagnostics/borrow_help/")
    assert skipped.skip_reason == (
        "--filter 'diagnostics/borrow_help/' selects no library-reader check")
    assert skipped.report() == [
        "Library-report gate SKIPPED: --filter 'diagnostics/borrow_help/' selects no "
        "library-reader check."]
