"""Hooks of the runner tests: the `runner` marker, and the refusal of a parallel run.

Every test in this directory gets the `runner` marker. `pytest.ini` leaves the marker out
of a plain run, so `pytest` is the fast Python layer and `pytest -m runner` is this one.

A runner test writes the checkout: it stages a fixture in `tests/`, builds the leak
interposer, or builds the stdlib and the helper libraries again. So the runner tests run
ONE instance at a time (#1071), and a runner test on a `pytest -n` worker fails at once.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# The import puts tests/ on sys.path before any module here is read.
import _harness  # noqa: F401

RUNNER_DIR = Path(__file__).resolve().parent
RUNNER_MARKER = "runner"
PARALLEL_REFUSAL = (
    "the runner tests run one instance at a time: they write the checkout. Run "
    "`uv run pytest -q -m runner` without `-n`, in a worktree that nothing else uses.")


def _in_runner_dir(item: pytest.Item) -> bool:
    try:
        Path(item.path).resolve().relative_to(RUNNER_DIR)
    except ValueError:
        return False
    return True


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _in_runner_dir(item):
            item.add_marker(RUNNER_MARKER)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if hasattr(item.config, "workerinput") and _in_runner_dir(item):
        pytest.fail(PARALLEL_REFUSAL, pytrace=False)
