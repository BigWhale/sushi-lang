"""Hooks of the runner tests: the `runner` marker, and the checkout lock for `pytest -n`.

Every test in this directory gets the `runner` marker, so `pytest -m "not runner"` is the
fast Python layer and `pytest -m runner` is this one.

Under `pytest-xdist` the workers share one checkout. A test marked `writes_the_checkout`
changes a thing that every other runner test reads: it puts a fixture into `tests/`, it
builds the leak interposer, or it starts the front end without `--skip-build` (which
builds the stdlib and the helper libraries again). Such a test holds an exclusive lock;
every other runner test holds a shared lock. A waiting writer stops new readers, so a
writer does not wait for the end of the run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

import pytest

# The import also puts tests/ on sys.path before any module here is read.
from _harness import checkout_lock

RUNNER_DIR = Path(__file__).resolve().parent
RUNNER_MARKER = "runner"
WRITER_MARKER = "writes_the_checkout"


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


def _lock_dir(config: pytest.Config) -> Optional[Path]:
    """The base temporary directory of the whole run, on a `pytest -n` worker; else None.

    xdist gives each worker the directory `<the run's base>/<worker id>`, so the parent is
    one directory for all the workers, and pytest removes it with its old runs.
    """
    if not hasattr(config, "workerinput") or not config.option.basetemp:
        return None
    return Path(config.option.basetemp).parent


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[None]:
    """Hold the lock over setup, call and teardown, so a module fixture is inside it."""
    lock_dir = _lock_dir(item.config)
    if lock_dir is None or not _in_runner_dir(item):
        yield
        return
    exclusive = item.get_closest_marker(WRITER_MARKER) is not None
    with checkout_lock(lock_dir, exclusive):
        yield

