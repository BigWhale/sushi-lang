"""The runner tests share one harness, carry one marker, and can run under `pytest -n`.

`_harness.py` holds the paths and one helper for each action: `run_single` runs one
fixture in this process, `run_tests` starts the front end. `conftest.py` gives every test
here the `runner` marker, so `pytest -m "not runner"` is the fast Python layer, and holds
`checkout_lock` around each test on a `pytest -n` worker.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

from _harness import PROJECT_ROOT, checkout_lock

RUNNER_DIR = Path(__file__).resolve().parent
RUNNER_PREFIX = "tests/unit/runner/"

# Each pattern is a second copy of a thing the harness holds.
REPEATS = {
    "the path preamble": re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[2\]"),
    "a sys.path insert": re.compile(r"sys\.path\.insert\("),
    "a run_single_test helper": re.compile(
        r"def _\w*\([^)]*\)[^:]*:\n(?:    .*\n)*?\s+return runner\.run_single_test\("),
    "a run_tests.py subprocess": re.compile(
        r"subprocess\.run\(\s*\[sys\.executable, str\((?:RUN_TESTS|TESTS_DIR / \"run_tests\.py\")\)"),
}


def _collected(marker: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", marker, "tests/unit"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    return [line for line in done.stdout.splitlines() if "::" in line]


def test_the_runner_marker_is_on_every_runner_test_and_on_no_other():
    fast = _collected("not runner")
    slow = _collected("runner")
    assert fast, "the control: tests outside runner/ are collected"
    assert slow, "the control: tests inside runner/ are collected"
    inside = [item for item in fast if item.startswith(RUNNER_PREFIX)]
    assert not inside, "a runner test has no `runner` marker:\n" + "\n".join(inside[:20])
    outside = [item for item in slow if not item.startswith(RUNNER_PREFIX)]
    assert not outside, "a test outside runner/ has the marker:\n" + "\n".join(outside[:20])


def test_no_runner_test_repeats_the_harness():
    found = []
    for module in sorted(RUNNER_DIR.glob("test_*.py")):
        if module.name == Path(__file__).name:
            continue
        source = module.read_text(encoding="utf-8")
        found += [f"{module.name}: {what}" for what, pattern in REPEATS.items()
                  if pattern.search(source)]
    assert not found, "use `_harness.py` instead:\n" + "\n".join(found)


def test_each_pattern_finds_the_copy_it_names():
    """The always-fires control for the scan above."""
    copies = {
        "the path preamble": "TESTS_DIR = Path(__file__).resolve().parents[2]\n",
        "a sys.path insert": "sys.path.insert(0, str(TESTS_DIR))\n",
        "a run_single_test helper": (
            "def _run(path: Path):\n"
            "    with enhanced_test_runner.TestRunner(TESTS_DIR) as runner:\n"
            "        return runner.run_single_test(path)\n"),
        "a run_tests.py subprocess": (
            "    return subprocess.run(\n"
            "        [sys.executable, str(RUN_TESTS), \"--skip-build\", *flags],\n"),
    }
    assert copies.keys() == REPEATS.keys()
    for what, source in copies.items():
        assert REPEATS[what].search(source), what


def _hold(lock_dir: Path, exclusive: bool, held: threading.Event,
          release: threading.Event) -> threading.Thread:
    def body() -> None:
        with checkout_lock(lock_dir, exclusive):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread


def test_the_checkout_lock_shares_readers_and_gives_a_writer_its_turn(tmp_path):
    first_held, first_release = threading.Event(), threading.Event()
    second_held, second_release = threading.Event(), threading.Event()
    writer_held, writer_release = threading.Event(), threading.Event()
    late_held, late_release = threading.Event(), threading.Event()

    threads = [_hold(tmp_path, False, first_held, first_release)]
    assert first_held.wait(5)
    threads.append(_hold(tmp_path, False, second_held, second_release))
    assert second_held.wait(5), "two readers must hold the lock at one time"

    threads.append(_hold(tmp_path, True, writer_held, writer_release))
    assert not writer_held.wait(0.5), "a writer must wait for the readers"
    threads.append(_hold(tmp_path, False, late_held, late_release))
    assert not late_held.wait(0.5), "a reader must not pass a waiting writer"

    first_release.set()
    second_release.set()
    assert writer_held.wait(5), "the writer must get the lock when the readers go"
    assert not late_held.wait(0.5), "a reader must wait while the writer holds the lock"
    writer_release.set()
    assert late_held.wait(5), "the reader must get the lock when the writer goes"
    late_release.set()
    for thread in threads:
        thread.join(5)
