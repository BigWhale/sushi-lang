"""The shared harness of the runner tests: the paths, and one helper for each action.

`run_single` runs one fixture through the one runner, in this process. `run_tests` starts
the front end `tests/run_tests.py` as a subprocess, as a developer or CI does; the caller
gives every flag, `--skip-build` included, so the build that a test asks for is visible
at the call.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

TESTS_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = TESTS_DIR.parent
RUN_TESTS = TESTS_DIR / "run_tests.py"
ENHANCED = TESTS_DIR / "enhanced_test_runner.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# tests/ is not a package; the harness modules import each other flat.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import enhanced_test_runner  # noqa: E402


def run_single(path: Path):
    """Run one fixture through `TestRunner.run_single_test` and give its result."""
    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:
        return runner.run_single_test(path)


def detail(result) -> str:
    """The two messages of a result, for an assertion message."""
    return f"{result.compilation_message}\n{result.runtime_message}"


def run_tests(*flags: str, env: Optional[Mapping[str, str]] = None,
              timeout: int = 900) -> subprocess.CompletedProcess:
    """Start `tests/run_tests.py` with `flags` in the checkout and capture its output."""
    return subprocess.run(
        [sys.executable, str(RUN_TESTS), *flags],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout,
        env=dict(os.environ if env is None else env),
    )
