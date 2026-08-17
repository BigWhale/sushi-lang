"""Benchmark corpus for the perf harness (P1-5)."""
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
    """Write a small two-unit project into *project_dir*; return the entry file."""
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
