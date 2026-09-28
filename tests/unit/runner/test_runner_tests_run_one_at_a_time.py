"""The runner tests run one instance at a time, and only when they are asked for (#1071).

They write the checkout, so a parallel run races. A plain `pytest` does not collect them,
`pytest -m runner` does, and a runner test on a `pytest -n` worker fails at once with the
rule. So no lock between runner tests is necessary.
"""
from __future__ import annotations

import subprocess
import sys

from _harness import PROJECT_ROOT

RUNNER_PREFIX = "tests/unit/runner/"
SMALL_MODULE = "tests/unit/runner/test_runner_tests_share_one_harness.py"


def _pytest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)


def test_a_plain_run_collects_no_runner_test():
    done = _pytest("--collect-only", "-q", "tests/unit")
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    items = [line for line in done.stdout.splitlines() if "::" in line]
    assert items, "the control: the Python layer is collected"
    inside = [item for item in items if item.startswith(RUNNER_PREFIX)]
    assert not inside, "a plain run collects a runner test:\n" + "\n".join(inside[:20])


def test_an_explicit_marker_collects_the_runner_tests():
    done = _pytest("--collect-only", "-q", "-m", "runner", SMALL_MODULE)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert any("::" in line for line in done.stdout.splitlines()), done.stdout[-3000:]


def test_a_runner_test_on_an_xdist_worker_fails_at_once():
    done = _pytest("-q", "-m", "runner", "-n", "2", SMALL_MODULE)
    output = done.stdout + done.stderr
    assert done.returncode != 0, output[-3000:]
    assert "one instance at a time" in output, output[-3000:]


def test_no_lock_is_left_between_runner_tests():
    words = ("checkout" + "_lock", "writes_the" + "_checkout")
    files = [PROJECT_ROOT / "pytest.ini", *sorted((PROJECT_ROOT / "tests").rglob("*.py"))]
    hits = [f"{path.relative_to(PROJECT_ROOT)}: {word}" for path in files
            for word in words if word in path.read_text(encoding="utf-8")]
    assert not hits, "\n".join(hits)
