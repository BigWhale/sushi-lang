"""Benchmark corpus for the perf harness (P1-5).

Defines the deterministic inputs the harness measures:

  - **single-file programs** under ``programs/`` -- pure-compute, stdlib-free,
    each stressing a different compiler surface (arithmetic/codegen,
    generic monomorphization, enum+match). Compiled with ``--no-incremental`` so
    the metric is pure end-to-end compile cost, free of cache effects.

  - **a multi-unit project** -- written fresh into a tmp dir, used for the
    cold-vs-warm-build metric. Warm rebuild exercising the ``__sushi_cache__``
    incremental path should be dramatically faster than the cold build; that
    ratio is the highest-signal guard on the incremental-compilation feature.

The corpus is intentionally small and stable: a handful of programs keeps the
CI cost low, and committing the exact sources (rather than sampling the live
test suite) means the baseline measures the same code every run.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

PROGRAMS_DIR = Path(__file__).parent / "programs"


def single_file_programs() -> List[Tuple[str, Path]]:
    """Return ``(metric_name, path)`` for each committed single-file benchmark."""
    programs = []
    for path in sorted(PROGRAMS_DIR.glob("bench_*.sushi")):
        # bench_arithmetic.sushi -> cold_compile:arithmetic
        stem = path.stem[len("bench_"):] if path.stem.startswith("bench_") else path.stem
        programs.append((f"cold_compile:{stem}", path))
    return programs


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else content + "\n"
    path.write_text(text, encoding="utf-8")


def make_project(project_dir: Path) -> str:
    """Write a small two-unit project into *project_dir*; return the entry file.

    Mirrors the fixture shape used by ``tests/unit/test_incremental.py``: a
    ``helpers/helper`` unit plus a ``main`` that imports it. Stdlib-free and
    enum-free (multi-unit fingerprinting still trips on enums, issue #26), so the
    project builds on a bare toolchain. The entry filename (``main.sushi``) is
    returned for the caller to pass to ``sushic``.
    """
    _write(project_dir / "helpers" / "helper.sushi", """\
const i32 BASE = 10

public fn doubled(i32 x) i32:
    return Result.Ok(x * 2)

public fn scaled(i32 x, i32 k) i32:
    return Result.Ok(x * k + BASE)
""")
    _write(project_dir / "main.sushi", """\
use "helpers/helper"

fn main() i32:
    let i32 a = doubled(21).realise(0)
    let i32 b = scaled(7, 3).realise(0)
    println(a + b)
    return Result.Ok(0)
""")
    return "main.sushi"
