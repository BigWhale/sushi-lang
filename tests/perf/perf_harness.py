"""Pure logic for the performance-regression harness (P1-5).

This module is deliberately free of subprocess / filesystem timing concerns so
it can be unit-tested in isolation (see ``test_perf_harness.py``). The actual
measurement (spawning ``sushic`` and timing it) lives in
``test_perf_regression.py``; this module only handles:

  - computing a median from a set of samples,
  - comparing measured medians against a stored baseline (with tolerance),
  - formatting a human-readable delta table,
  - loading / saving the per-platform ``baseline.json``.

Baselines are keyed by platform (e.g. ``darwin-arm64`` vs ``linux-x86_64``) so
that arm64-macOS and x86_64-Linux timings never cross-contaminate. A metric with
no baseline for the current platform is reported, never failed -- which is what
keeps the harness in *report mode* until a baseline is deliberately captured.
"""
from __future__ import annotations

import json
import platform
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Bumped only on an incompatible change to baseline.json's shape.
BASELINE_VERSION = 1

# Default tolerance used both when writing a fresh baseline and when a stored
# metric omits its own tolerance. Generous on purpose: micro-benchmarks on
# shared CI runners are noisy, and report-mode-first means a false alarm costs
# more trust than a missed 25% regression costs signal.
DEFAULT_TOLERANCE_PCT = 25.0


def platform_key() -> str:
    """Return a stable ``<system>-<machine>`` key, e.g. ``darwin-arm64``."""
    system = platform.system().lower()      # 'darwin' / 'linux' / 'windows'
    machine = platform.machine().lower()    # 'arm64' / 'x86_64' / ...
    return f"{system}-{machine}"


def median_ms(samples: List[float]) -> float:
    """Median of *samples* (raises ValueError on empty input)."""
    if not samples:
        raise ValueError("median_ms requires at least one sample")
    return float(statistics.median(samples))


@dataclass
class MetricResult:
    """A measured metric: its name, median, and the raw samples behind it."""
    name: str
    median_ms: float
    samples: List[float]


@dataclass
class Delta:
    """A measured metric compared against its baseline (if any)."""
    name: str
    current_ms: float
    baseline_ms: Optional[float]      # None when no baseline exists for this metric
    delta_pct: Optional[float]        # None when no baseline
    tolerance_pct: float
    regressed: bool                   # current exceeds baseline * (1 + tol)

    @property
    def has_baseline(self) -> bool:
        return self.baseline_ms is not None


def compare(
    results: List[MetricResult],
    baseline_metrics: Dict[str, dict],
    default_tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
) -> List[Delta]:
    """Compare measured *results* against *baseline_metrics*.

    *baseline_metrics* maps ``metric name -> {"median_ms": float,
    "tolerance_pct": float, ...}`` (the per-platform ``metrics`` block). A metric
    with no entry yields a baseline-less Delta (``regressed=False``).
    """
    deltas: List[Delta] = []
    for r in results:
        entry = baseline_metrics.get(r.name)
        if entry is None:
            deltas.append(Delta(r.name, r.median_ms, None, None, default_tolerance_pct, False))
            continue
        base_ms = float(entry["median_ms"])
        tol = float(entry.get("tolerance_pct", default_tolerance_pct))
        if base_ms > 0:
            delta_pct = (r.median_ms - base_ms) / base_ms * 100.0
        else:
            delta_pct = 0.0
        regressed = r.median_ms > base_ms * (1.0 + tol / 100.0)
        deltas.append(Delta(r.name, r.median_ms, base_ms, delta_pct, tol, regressed))
    return deltas


def format_table(deltas: List[Delta], plat: str) -> str:
    """Render *deltas* as a fixed-width delta table for the captured pytest log."""
    lines = [
        f"=== Perf report ({plat}) ===",
        f"{'metric':<34}{'current':>11}{'baseline':>11}{'delta':>9}{'tol':>6}  status",
    ]
    for d in deltas:
        cur = f"{d.current_ms:.1f}ms"
        if not d.has_baseline:
            lines.append(f"{d.name:<34}{cur:>11}{'-':>11}{'-':>9}{'-':>6}  no-baseline")
            continue
        base = f"{d.baseline_ms:.1f}ms"
        delta = f"{d.delta_pct:+.1f}%"
        tol = f"{d.tolerance_pct:.0f}%"
        if d.regressed:
            status = "REGRESSED"
        elif d.delta_pct is not None and d.delta_pct < 0:
            status = "ok (faster)"
        else:
            status = "ok"
        lines.append(f"{d.name:<34}{cur:>11}{base:>11}{delta:>9}{tol:>6}  {status}")
    lines.append("NOTE: report mode -- this harness never fails the build (P1-5 phase 1).")
    return "\n".join(lines)


def load_baseline(path: Path, plat: str) -> Dict[str, dict]:
    """Return the ``metrics`` dict for *plat*, or ``{}`` if absent/missing.

    An absent file or an absent platform section both mean "no baseline" -- the
    caller treats every metric as baseline-less (report only).
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("platforms", {}).get(plat, {}).get("metrics", {})


def save_baseline(
    path: Path,
    plat: str,
    results: List[MetricResult],
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
) -> None:
    """Write/update the *plat* section of ``baseline.json`` from *results*.

    Other platforms' sections are preserved -- refreshing the macOS baseline
    must not wipe the Linux one. Output is deterministic (sorted keys) so a
    baseline refresh produces a clean, reviewable diff.
    """
    data: dict = {"version": BASELINE_VERSION, "platforms": {}}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("version", BASELINE_VERSION)
        data.setdefault("platforms", {})
    data["platforms"][plat] = {
        "metrics": {
            r.name: {
                "median_ms": round(r.median_ms, 3),
                "tolerance_pct": tolerance_pct,
                "samples": len(r.samples),
            }
            for r in results
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
