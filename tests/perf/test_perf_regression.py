"""Performance-regression harness -- REPORT MODE (P1-5, phase 1).

Measures end-to-end ``sushic`` compile time over a small, committed corpus and
compares the medians against a per-platform baseline. In this phase the harness
is **non-gating**: a timing delta (even a large one) is reported, never failed.
The only hard failure is a corpus program that stops *compiling* -- a real
correctness signal, not a perf one.

Metrics
  - ``cold_compile:<prog>`` -- end-to-end compile of each single-file program
    with ``--no-incremental`` (pure compile cost, no cache effects).
  - ``cold_build:project`` / ``warm_build:project`` -- a multi-unit build with a
    wiped vs. populated ``__sushi_cache__``. The warm/cold ratio is the headline
    guard on the incremental-compilation feature: warm should be far cheaper.

Knobs (env)
  - ``SUSHI_PERF_SKIP=1``    -- skip the measurement entirely.
  - ``SUSHI_PERF_SAMPLES=N`` -- samples per metric (median-of-N; default 5).

Refreshing the baseline is deliberate: ``pytest tests/perf --update-baseline``
rewrites the current platform's section of ``baselines/baseline.json``; commit
the diff. See ``README.md`` for the documented flip-to-gating criterion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Tuple

import pytest

import bench_corpus
import perf_harness as ph

BASELINE_PATH = Path(__file__).parent / "baselines" / "baseline.json"


def _samples() -> int:
    try:
        return max(1, int(os.environ.get("SUSHI_PERF_SAMPLES", "5")))
    except ValueError:
        return 5


def _timed_run(cmd: List[str], cwd: Path) -> Tuple[float, subprocess.CompletedProcess]:
    """Run *cmd* in *cwd*, returning (elapsed_ms, completed_process)."""
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms, proc


def _measure(cmd: List[str], cwd: Path, samples: int,
             reset=None) -> Tuple[List[float], subprocess.CompletedProcess]:
    """Time *cmd* *samples* times. *reset* (optional) runs before each sample.

    Stops early on the first non-zero exit so a broken corpus surfaces fast; the
    returned process lets the caller report the failure.
    """
    times: List[float] = []
    last = None
    for _ in range(samples):
        if reset is not None:
            reset()
        elapsed, last = _timed_run(cmd, cwd)
        times.append(elapsed)
        if last.returncode != 0:
            break
    return times, last


def test_perf_report(tmp_path, request):
    if os.environ.get("SUSHI_PERF_SKIP"):
        pytest.skip("SUSHI_PERF_SKIP set")

    samples = _samples()
    results: List[ph.MetricResult] = []
    failures: List[Tuple[str, str]] = []

    # -- single-file cold compiles ------------------------------------------ #
    for metric, src in bench_corpus.single_file_programs():
        work = tmp_path / metric.replace(":", "_")
        work.mkdir(parents=True, exist_ok=True)
        (work / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        cmd = ["sushic", src.name, "-o", "out", "--no-incremental"]
        times, last = _measure(cmd, work, samples)
        if last.returncode != 0:
            failures.append((metric, last.stderr))
            continue
        results.append(ph.MetricResult(metric, ph.median_ms(times), times))

    # -- multi-unit project: cold vs warm ----------------------------------- #
    proj = tmp_path / "project"
    proj.mkdir(parents=True, exist_ok=True)
    entry = bench_corpus.make_project(proj)
    build = ["sushic", entry, "-o", "out"]
    cache_dir = proj / "__sushi_cache__"

    cold_times, cold_last = _measure(
        build, proj, samples, reset=lambda: shutil.rmtree(cache_dir, ignore_errors=True)
    )
    if cold_last.returncode != 0:
        failures.append(("cold_build:project", cold_last.stderr))
    else:
        results.append(ph.MetricResult("cold_build:project", ph.median_ms(cold_times), cold_times))

        # Ensure a populated cache, then measure warm (no-source-change) rebuilds.
        _timed_run(build, proj)
        warm_times, warm_last = _measure(build, proj, samples)
        if warm_last.returncode != 0:
            failures.append(("warm_build:project", warm_last.stderr))
        else:
            results.append(ph.MetricResult("warm_build:project", ph.median_ms(warm_times), warm_times))

    # -- baseline: compare (report) or refresh ------------------------------ #
    plat = ph.platform_key()
    if request.config.getoption("--update-baseline"):
        if results:
            ph.save_baseline(BASELINE_PATH, plat, results)
        report = (f"baseline refreshed for {plat}: "
                  f"{', '.join(r.name for r in results) or '(no metrics)'}")
    else:
        baseline_metrics = ph.load_baseline(BASELINE_PATH, plat)
        deltas = ph.compare(results, baseline_metrics)
        report = ph.format_table(deltas, plat)

    # Surfaced via the pytest_terminal_summary hook (visible under -q).
    request.config._perf_report = report

    # Report mode: timing never fails. The corpus must still COMPILE, though --
    # a benchmark that stops building is a correctness regression worth failing.
    assert not failures, "perf corpus failed to compile:\n" + "\n".join(
        f"--- {name} ---\n{err}" for name, err in failures
    )
