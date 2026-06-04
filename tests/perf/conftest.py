"""Pytest options for the perf harness (P1-5).

Adds ``--update-baseline``: when passed, the perf-regression test rewrites the
current platform's section of ``baselines/baseline.json`` from the measured
medians instead of comparing against the stored ones. Refreshing the baseline is
therefore a deliberate, reviewed act (run the flag, commit the diff) -- never
automatic.
"""
from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--update-baseline",
        action="store_true",
        default=False,
        help="(perf) Rewrite the current platform's perf baseline from measured medians.",
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the perf delta table in the terminal summary.

    The perf test stashes its rendered report on ``config._perf_report``; this
    hook surfaces it even under ``pytest -q`` (plain ``print`` would be swallowed
    by capture on a passing test), so the report mode is actually visible in CI.
    """
    report = getattr(config, "_perf_report", None)
    if report:
        terminalreporter.section("perf report")
        terminalreporter.write_line(report)
