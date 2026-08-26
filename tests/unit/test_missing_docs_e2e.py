"""`--warn-missing-docs` end to end: the CLI flag, the exit code, and what reaches stderr.

Every other phase-5 module calls the lint directly. This one spawns `./sushic`, which is
the only thing that proves the flag is wired from `cli.py` through `pipeline.py` to the
analyzer, and that a warning still exits 1.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]

SOURCE = """\
struct Ship:
    i32 hull

##:
Reads a ship's hull rating, with a bonus added.

- Parameter ship: The ship to read.
:##
fn hull_of(Ship ship, i32 bonus) i32:
    return Result.Ok(ship.hull + bonus)

fn main() i32:
    let Ship s = Ship(7)
    println("{hull_of(s, 1).realise(0)}")
    return Result.Ok(0)
"""


def _compile(tmp_path: Path, *flags: str) -> subprocess.CompletedProcess:
    source = tmp_path / "ship.sushi"
    source.write_text(SOURCE, encoding="utf-8")
    return subprocess.run(
        ["./sushic", str(source), "-o", str(tmp_path / "ship"), *flags],
        capture_output=True, text=True, cwd=REPO, timeout=120,
        env={**os.environ, "NO_COLOR": "1"})


def test_the_flag_is_accepted(tmp_path):
    result = _compile(tmp_path, "--warn-missing-docs")
    assert "unrecognized arguments" not in result.stderr


def test_the_lint_reports_and_exits_one(tmp_path):
    result = _compile(tmp_path, "--warn-missing-docs")
    assert result.returncode == 1, result.stderr
    for code in ("CW7002", "CW7003", "CW7004", "CW7006"):
        assert code in result.stderr, f"{code} missing from:\n{result.stderr}"


def test_the_same_source_is_silent_without_the_flag(tmp_path):
    result = _compile(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "CW70" not in result.stderr
