"""Pytest options for the perf harness (P1-5)."""
from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--update-baseline",
        action="store_true",
        default=False,
        help="(perf) Rewrite the current platform's perf baseline from measured medians.",
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the perf delta table in the terminal summary."""
    report = getattr(config, "_perf_report", None)
    if report:
        terminalreporter.section("perf report")
        terminalreporter.write_line(report)
