"""Unit tests for the pure perf-harness logic (P1-5).

These are fast, deterministic tests of median/compare/format/baseline-IO -- no
subprocess, no timing. They guard the comparison logic that decides whether a
metric counts as a regression, independently of any actual measurement.
"""
from __future__ import annotations

import json

import pytest

import perf_harness as ph


# --------------------------------------------------------------------------- #
# median_ms
# --------------------------------------------------------------------------- #

def test_median_odd():
    assert ph.median_ms([3.0, 1.0, 2.0]) == 2.0


def test_median_even_averages_middle():
    assert ph.median_ms([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_median_empty_raises():
    with pytest.raises(ValueError):
        ph.median_ms([])


# --------------------------------------------------------------------------- #
# platform_key
# --------------------------------------------------------------------------- #

def test_platform_key_shape():
    key = ph.platform_key()
    assert "-" in key
    system, _, machine = key.partition("-")
    assert system and machine
    assert key == key.lower()


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #

def _result(name, ms):
    return ph.MetricResult(name=name, median_ms=ms, samples=[ms])


def test_compare_no_baseline_is_not_regression():
    deltas = ph.compare([_result("a", 100.0)], baseline_metrics={})
    assert len(deltas) == 1
    d = deltas[0]
    assert not d.has_baseline
    assert d.baseline_ms is None
    assert d.delta_pct is None
    assert d.regressed is False


def test_compare_within_tolerance_not_regressed():
    base = {"a": {"median_ms": 100.0, "tolerance_pct": 25.0}}
    # +20% is within a 25% tolerance.
    d = ph.compare([_result("a", 120.0)], base)[0]
    assert d.regressed is False
    assert d.delta_pct == pytest.approx(20.0)


def test_compare_over_tolerance_regressed():
    base = {"a": {"median_ms": 100.0, "tolerance_pct": 25.0}}
    # +30% exceeds a 25% tolerance.
    d = ph.compare([_result("a", 130.0)], base)[0]
    assert d.regressed is True
    assert d.delta_pct == pytest.approx(30.0)


def test_compare_faster_than_baseline_not_regressed():
    base = {"a": {"median_ms": 100.0, "tolerance_pct": 25.0}}
    d = ph.compare([_result("a", 40.0)], base)[0]
    assert d.regressed is False
    assert d.delta_pct == pytest.approx(-60.0)


def test_compare_uses_default_tolerance_when_entry_omits_it():
    base = {"a": {"median_ms": 100.0}}  # no per-metric tolerance
    d = ph.compare([_result("a", 110.0)], base, default_tolerance_pct=5.0)[0]
    # 5% default tolerance -> +10% is a regression.
    assert d.tolerance_pct == 5.0
    assert d.regressed is True


def test_compare_zero_baseline_does_not_divide_by_zero():
    base = {"a": {"median_ms": 0.0, "tolerance_pct": 25.0}}
    d = ph.compare([_result("a", 5.0)], base)[0]
    assert d.delta_pct == 0.0
    # 5.0 > 0 * 1.25 == 0, so it is technically a regression; the guard is only
    # against a ZeroDivisionError, which must not happen.
    assert d.regressed is True


# --------------------------------------------------------------------------- #
# format_table
# --------------------------------------------------------------------------- #

def test_format_table_marks_states():
    deltas = [
        ph.Delta("regress", 130.0, 100.0, 30.0, 25.0, True),
        ph.Delta("faster", 40.0, 100.0, -60.0, 25.0, False),
        ph.Delta("fresh", 50.0, None, None, 25.0, False),
    ]
    table = ph.format_table(deltas, "linux-x86_64")
    assert "linux-x86_64" in table
    assert "REGRESSED" in table
    assert "ok (faster)" in table
    assert "no-baseline" in table
    assert "report mode" in table


# --------------------------------------------------------------------------- #
# load_baseline / save_baseline
# --------------------------------------------------------------------------- #

def test_load_missing_file_returns_empty(tmp_path):
    assert ph.load_baseline(tmp_path / "nope.json", "linux-x86_64") == {}


def test_load_missing_platform_returns_empty(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"version": 1, "platforms": {"darwin-arm64": {"metrics": {"a": {"median_ms": 1.0}}}}}))
    assert ph.load_baseline(p, "linux-x86_64") == {}


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "b.json"
    results = [ph.MetricResult("a", 12.345678, [1.0, 2.0]), ph.MetricResult("b", 99.0, [99.0])]
    ph.save_baseline(p, "linux-x86_64", results, tolerance_pct=20.0)
    metrics = ph.load_baseline(p, "linux-x86_64")
    assert metrics["a"]["median_ms"] == pytest.approx(12.346, abs=1e-3)
    assert metrics["a"]["tolerance_pct"] == 20.0
    assert metrics["a"]["samples"] == 2
    assert metrics["b"]["median_ms"] == 99.0


def test_save_preserves_other_platforms(tmp_path):
    p = tmp_path / "b.json"
    ph.save_baseline(p, "darwin-arm64", [ph.MetricResult("a", 1.0, [1.0])])
    ph.save_baseline(p, "linux-x86_64", [ph.MetricResult("a", 2.0, [2.0])])
    data = json.loads(p.read_text())
    assert set(data["platforms"]) == {"darwin-arm64", "linux-x86_64"}
    assert data["platforms"]["darwin-arm64"]["metrics"]["a"]["median_ms"] == 1.0
    assert data["platforms"]["linux-x86_64"]["metrics"]["a"]["median_ms"] == 2.0
