# Performance regression harness (P1-5)

Measures `sushic` compile time over a small committed corpus and compares the
medians against a per-platform baseline. It is the regression guard the project
previously lacked: a pass going quadratic or a codegen path regressing was
invisible until someone noticed a slow build.

## Phase 1 — report mode (current)

**The harness is non-gating.** A timing delta is *reported*, never *failed*. The
only hard failure is a corpus program that stops **compiling** — a correctness
signal, not a perf one. This mirrors the report-mode-first pattern used by the
stdout (`test_stdout_coverage.py`) and diagnostics (`test_diagnostic_coverage.py`)
ratchets: ship the measurement, establish a noise floor, *then* flip to gating.

The delta table prints in the pytest terminal summary (visible under `-q`):

```
=== Perf report (darwin-arm64) ===
metric                                current   baseline    delta   tol  status
cold_compile:arithmetic               354.4ms    354.2ms    +0.1%   25%  ok
cold_build:project                    480.2ms    469.0ms    +2.4%   25%  ok
warm_build:project                    480.9ms    449.0ms    +7.1%   25%  ok
NOTE: report mode -- this harness never fails the build (P1-5 phase 1).
```

## Metrics

| Metric | What it times |
|---|---|
| `cold_compile:<prog>` | End-to-end compile of a single-file program with `--no-incremental` (pure compile cost, no cache). One per file in `programs/`. |
| `cold_build:project` | Multi-unit build with a wiped `__sushi_cache__`. |
| `warm_build:project` | Same build, unchanged sources, populated cache. The warm/cold pair guards the incremental-compilation build path. |

Each metric is the **median of N** runs (default 5) to damp noise.

## Running

```bash
uv run pytest tests/perf -q                 # measure + report (never fails on timing)
SUSHI_PERF_SAMPLES=3 uv run pytest tests/perf -q   # fewer samples (faster)
SUSHI_PERF_SKIP=1   uv run pytest tests/perf -q    # skip measurement entirely
```

The pure comparison logic (median, baseline compare, formatting, baseline IO)
has fast unit tests in `test_perf_harness.py` — no subprocess, no timing.

## Baselines

`baselines/baseline.json` stores medians **keyed by platform**
(`darwin-arm64`, `linux-x86_64`, …) so arm64-macOS and x86_64-Linux timings
never cross-contaminate. A metric with no entry for the current platform is
reported as `no-baseline` and never compared — which is also the state CI starts
in until a baseline is deliberately captured there.

Refreshing a baseline is explicit and reviewed:

```bash
uv run pytest tests/perf --update-baseline -q   # rewrite THIS platform's section
git add tests/perf/baselines/baseline.json      # commit the diff
```

`--update-baseline` only touches the current platform's section; other
platforms are preserved. The committed `darwin-arm64` baseline is a starting
reference captured on a dev machine, not an authoritative number — report mode
means it never gates.

## Flip-to-gating criterion (phase 2+)

Turn a metric from report-only into a gate **only after** several CI runs on the
same platform show its run-to-run spread stays comfortably inside the tolerance
(currently 25%). Gate the most stable metrics first (`warm_build`, and the
per-pass numbers below once they exist); keep noisy absolute wall-time metrics in
report mode. Gating is a deliberate change to `test_perf_regression.py` (assert
`not [d for d in deltas if d.regressed]`), made per-metric, never wholesale.

## Known limitation — and the deferred precise layer

End-to-end wall time is **dominated by fixed `sushic` startup** (~300ms of Python
interpreter + import on this toolchain), and semantic analysis is always
whole-program, so the per-unit `.o` cache only saves codegen. Consequently:

- The warm/cold gap is modest at small corpus sizes (startup swamps the saving).
- A moderate single-pass regression can hide inside the fixed-cost floor.

Phase 1 still catches **gross** regressions (a metric blowing past 25%). The
precise layer — per-pass `duration_ms`, which `SemanticPipeline` already computes
but only prints to stderr under an unwired `verbose` flag — is **deferred to a
follow-up**: expose it via a `sushic --timing-json` flag, record per-pass numbers
here, and gate those (they exclude startup, so they're both sensitive and
stable). That is the natural P1-5 phase 2.

## Files

- `perf_harness.py` — pure logic (median, compare, format, baseline IO). Unit-tested.
- `bench_corpus.py` — corpus: single-file programs + the multi-unit project builder.
- `programs/bench_*.sushi` — committed, stdlib-free, deterministic benchmark inputs.
- `test_perf_regression.py` — report-mode measurement test (the harness).
- `test_perf_harness.py` — unit tests for the pure logic.
- `conftest.py` — `--update-baseline` option + the terminal-summary report hook.
- `baselines/baseline.json` — per-platform medians.
