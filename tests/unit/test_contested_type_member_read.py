"""A member read on a type name the unit LOST is not measured against the winner (#738).

A TYPE is one name for the whole program. When a consumer declares a struct a library
already declares, the consumer hears CE0004 at its declaration, and that is the one
fault. The member-read rule then read the WINNER's fields and told the consumer that
its own field does not exist: CE2106, three lines below the real fault. The enum twin
was already clean, because the match rule asks `name_is_contested` before it speaks.

Both library paths reach it: a GENERIC type of a binary library (seeded before the
collect loop, #728) and a CONCRETE type of a source library (an ordinary unit).

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is ABSENT, so the one
diagnostic is pinned here, on the whole rendered output.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic


GENERIC_LIB = """\
public struct Crate@(T):
    T held
"""

CONCRETE_LIB = """\
public struct Crate:
    i32 held
"""

GENERIC_CLASH = """\
use <lib/cratelib>

struct Crate@(T):
    T mine

fn main() i32:
    let Crate@(i32) c = Crate(1)
    println("{c.mine}")
    return Result.Ok(0)
"""

CONCRETE_CLASH = """\
use <lib/cratelib>

struct Crate:
    i32 mine

fn main() i32:
    let Crate c = Crate(1)
    println("{c.mine}")
    return Result.Ok(0)
"""

# The winner's own misspelled read: the library's `Crate` is the unit's only one, so
# the rule speaks as it always did.
WINNER_TYPO = """\
use <lib/cratelib>

fn main() i32:
    let Crate c = Crate(1)
    println("{c.mine}")
    return Result.Ok(0)
"""


def _build_lib(tmp_path: Path, source: str, kind: str) -> dict:
    """Build `source` as `cratelib.slib` of `kind` and return the consumer's environment."""
    libs_dir = tmp_path / f"libs_{kind}"
    libs_dir.mkdir(parents=True, exist_ok=True)
    lib_src = tmp_path / f"cratelib_{kind}.sushi"
    lib_src.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / "cratelib.slib")],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"}


def _consume(tmp_path: Path, env: dict, program: str, name: str) -> tuple[int, str]:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "main.sushi").write_text(program, encoding="utf-8")
    result = subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.returncode, result.stdout + result.stderr


def _codes(out: str) -> list[str]:
    return re.findall(r"\b(C[EW]\d{4})\b", out)


@needs_sushic
@pytest.mark.parametrize("kind", ["binary", "source"])
def test_generic_type_loser_hears_the_duplicate_alone(tmp_path, kind):
    """A generic `Crate@(T)` beside a library's: CE0004 once, and no CE2106."""
    env = _build_lib(tmp_path, GENERIC_LIB, kind)
    code, out = _consume(tmp_path, env, GENERIC_CLASH, f"generic_{kind}")

    assert code == 2, out
    assert _codes(out) == ["CE0004"], out


@needs_sushic
def test_concrete_source_type_loser_hears_the_duplicate_alone(tmp_path):
    """A concrete `Crate` beside a SOURCE library's: CE0004 once, and no CE2106."""
    env = _build_lib(tmp_path, CONCRETE_LIB, "source")
    code, out = _consume(tmp_path, env, CONCRETE_CLASH, "concrete_source")

    assert code == 2, out
    assert _codes(out) == ["CE0004"], out


@needs_sushic
@pytest.mark.parametrize("kind", ["binary", "source"])
def test_the_winner_still_hears_an_unknown_field(tmp_path, kind):
    """The control: a unit that declared nothing is told its field does not exist."""
    env = _build_lib(tmp_path, CONCRETE_LIB, kind)
    code, out = _consume(tmp_path, env, WINNER_TYPO, f"winner_{kind}")

    assert code == 2, out
    assert _codes(out) == ["CE2106"], out
