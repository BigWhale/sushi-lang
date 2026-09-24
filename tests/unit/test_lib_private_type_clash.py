"""A consumer cannot declare a type name a BINARY library's closure ships privately (#761).

A private type of a binary library travels when a public template body names it: the
export closure ships it as source, and the `libraries` step registers it so the
monomorphized body can name it. The PUBLIC arm refuses a consumer's declaration of an
exported name with CE3011 (#739). The PRIVATE arm skipped the name in silence, and the
transplanted body was then measured against the CONSUMER's layout: CE2027, CE2028 and
CE2106 about the library's own code, located in a template the user never wrote.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is ABSENT, so the one
diagnostic is pinned here, on the whole rendered output.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic


PRIVATE_LIB = """\
struct Acc:
    i32 total

public fn compute@(T)(nom T a, nom T b) T:
    let Acc acc = Acc(6)
    if (acc.total == 6):
        return Result.Ok(a)
    return Result.Ok(b)
"""

STRUCT_CLASH = """\
use <lib/privlib>

struct Acc:
    string name
    i32 a

fn main() i32:
    let Acc mine = Acc("x", 5)
    println("{mine.a}")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""

ENUM_CLASH = """\
use <lib/privlib>

enum Acc:
    Empty

fn main() i32:
    let Acc mine = Acc.Empty
    match mine:
        Acc.Empty -> println("empty")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""

NO_CLASH = """\
use <lib/privlib>

fn main() i32:
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""


def _build_lib(tmp_path: Path) -> dict:
    """Build `PRIVATE_LIB` as a BINARY `.slib` and return the consumer's environment."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(parents=True, exist_ok=True)
    lib_src = tmp_path / "privlib.sushi"
    lib_src.write_text(PRIVATE_LIB, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", "binary", "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / "privlib.slib")],
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
def test_private_struct_name_is_refused_once(tmp_path):
    """One CE3011 at the consumer's struct, naming the library, and nothing else."""
    env = _build_lib(tmp_path)
    code, out = _consume(tmp_path, env, STRUCT_CLASH, "struct_clash")

    assert code == 2, out
    assert _codes(out) == ["CE3011"], out
    assert "privlib" in out, out
    assert "main.sushi:3:" in out, out


@needs_sushic
def test_private_struct_name_is_refused_for_a_consumer_enum(tmp_path):
    """The consumer's ENUM of that name is the same clash: a type is one name."""
    env = _build_lib(tmp_path)
    code, out = _consume(tmp_path, env, ENUM_CLASH, "enum_clash")

    assert code == 2, out
    assert _codes(out) == ["CE3011"], out
    assert "enum 'Acc'" in out, out


@needs_sushic
def test_private_type_still_reaches_the_template_body(tmp_path):
    """The control: a consumer that declares nothing runs the template over `Acc`."""
    env = _build_lib(tmp_path)
    code, out = _consume(tmp_path, env, NO_CLASH, "no_clash")

    assert code == 0, out
    assert _codes(out) == [], out
    run = subprocess.run([str(tmp_path / "no_clash" / "out")],
                         capture_output=True, text=True)
    assert run.stdout == "10\n", run.stdout + run.stderr
