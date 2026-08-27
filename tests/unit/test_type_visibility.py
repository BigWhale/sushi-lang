"""A struct and an enum are private unless marked, and one fence answers for both.

Ruling 1 of `docs/design/visibility.md`. Type identity is nominal, so visibility never
lives on the type (`docs/design/type-identity.md`): the fence asks the visibility table
about a NAME, from the one type funnel every named type already passes through.

A source library is the interesting case, because its units are ordinary units at the
consumer. Naming its private type is CE3005; declaring the same name is CE3011, and a
name it DOES export stays the plain duplicate, because one name is one shape.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")

LIBRARY = """\
struct Point:
    i32 x
    i32 y

public struct Line:
    i32 length

public fn origin_sum() i32:
    let Point p = Point(0, 0)
    return Result.Ok(p.x + p.y)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_source_lib(tmp_path: Path, source: str, name: str) -> dict:
    libs = tmp_path / "libs"
    libs.mkdir(exist_ok=True)
    src = tmp_path / f"{name}.sushi"
    _write(src, source)
    built = subprocess.run(
        ["sushic", "--lib", "--lib-kind", "source", "--lib-version", "1.0.0",
         str(src), "-o", str(libs / f"{name}.slib")],
        cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs), "NO_COLOR": "1"}


def _consume(tmp_path: Path, env: dict, program: str) -> tuple[str, Path]:
    project = tmp_path / "prog"
    _write(project / "main.sushi", program)
    binary = project / "out"
    result = subprocess.run(["sushic", "main.sushi", "-o", str(binary)],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.stdout + result.stderr, binary


@needs_sushic
def test_a_librarys_private_struct_cannot_be_named(tmp_path):
    env = _build_source_lib(tmp_path, LIBRARY, "geolib")
    out, _binary = _consume(tmp_path, env, """\
use <lib/geolib>

fn main() i32:
    let Point p = Point(1, 2)
    println("{p.x}")
    return Result.Ok(0)
""")
    assert "CE3005" in out, out
    assert "private struct 'Point'" in out, out


@needs_sushic
def test_a_librarys_public_struct_can_be_named(tmp_path):
    env = _build_source_lib(tmp_path, LIBRARY, "linelib")
    out, binary = _consume(tmp_path, env, """\
use <lib/linelib>

fn main() i32:
    let Line l = Line(9)
    println("{l.length} {origin_sum().realise(0)}")
    return Result.Ok(0)
""")
    assert "error" not in out, out
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.stdout == "9 0\n", ran.stdout


@needs_sushic
def test_a_library_units_own_body_is_never_the_consumers_problem(tmp_path):
    """A pack template's `...Ts` resolves to the CONSUMER's private type.

    The substitution happens in the library's unit, which is compiled at the consumer, so
    the fence would report the consumer's own private enum against library source the
    consumer never wrote. A library unit's bodies are exempt for the same reason the
    `docs` pass skips them whole.
    """
    env = _build_source_lib(tmp_path, """\
public perk Display:
    fn display() string

public fn show_all@(...Ts: Display)(...Ts args) ~:
    expand(a in args):
        println(a.display())
    return Result.Ok(~)
""", "packlib")
    out, binary = _consume(tmp_path, env, """\
use <lib/packlib>

enum Colour:
    Red()

extend Colour with Display:
    fn display() string:
        match self:
            Colour.Red() -> return "red"

fn main() i32:
    show_all(Colour.Red())
    return Result.Ok(0)
""")
    assert "CE3005" not in out, out
    assert "error" not in out, out
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.stdout == "red\n", ran.stdout
