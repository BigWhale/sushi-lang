"""A consumer type refused beside a library's type is not measured against it (#814).

A TYPE is one name for the whole program. When a consumer declares a name a library
already declares, the consumer hears one diagnostic at its declaration: CE3011 for a
library's PRIVATE type, CE0004 or CE0006 for a PUBLIC one or another unit's. The uses of that name in
the consumer are uses of a type the user cannot have, and they must not be judged
against the library's declaration.

Three paths reached the cascade:

- a SOURCE library's private type: the consumer's construction heard CE2028, and a
  consumer ENUM heard CE3005, CE2105 and CE2048 about its own variant and match;
- a BINARY library's private GENERIC template, which is seeded before the collect
  pass: CE0004 without the library's name, then CE2027, CE2028 and CE2106;
- a unit's ENUM beside another unit's STRUCT of the same name: the contest was keyed
  on the kind, so the struct winner was not seen as the enum's loss, and the loser
  heard CE3005 about the winner's privacy.

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


PRIVATE_STRUCT_LIB = """\
struct Acc:
    i32 total

public fn compute@(T)(nom T a, nom T b) T:
    let Acc acc = Acc(6)
    if (acc.total == 6):
        return Result.Ok(a)
    return Result.Ok(b)
"""

PRIVATE_TEMPLATE_LIB = """\
struct Acc@(T):
    T item

public fn compute@(T)(nom T a, nom T b) T:
    let Acc@(i32) acc = Acc(6)
    if (acc.item == 6):
        return Result.Ok(a)
    return Result.Ok(b)
"""

STRUCT_CLASH = """\
use <lib/acclib>

struct Acc:
    string name
    i32 a

fn main() i32:
    let Acc mine = Acc("x", 5)
    println("{mine.a}")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""

GENERIC_CLASH = """\
use <lib/acclib>

struct Acc@(T):
    string name
    T a

fn main() i32:
    let Acc@(i32) mine = Acc("x", 5)
    println("{mine.a}")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""

ENUM_CLASH = """\
use <lib/acclib>

enum Acc:
    Empty

fn main() i32:
    let Acc mine = Acc.Empty
    match mine:
        Acc.Empty -> println("empty")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""

# The control: the consumer owns its `Mine` alone, so a misspelled field is still
# the ordinary CE2106 beside a library that clashes with nothing.
OWN_TYPE_TYPO = """\
use <lib/acclib>

struct Mine:
    i32 a

fn main() i32:
    let Mine m = Mine(5)
    println("{m.b}")
    println("{compute(nom 10, nom 20).realise(99)}")
    return Result.Ok(0)
"""


def _build_lib(tmp_path: Path, source: str, kind: str) -> dict:
    """Build `source` as `acclib.slib` of `kind` and return the consumer's environment."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(parents=True, exist_ok=True)
    lib_src = tmp_path / "acclib.sushi"
    lib_src.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / "acclib.slib")],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"}


def _consume(tmp_path: Path, env: dict, program: str) -> tuple[int, str]:
    project = tmp_path / "consumer"
    project.mkdir(parents=True, exist_ok=True)
    (project / "main.sushi").write_text(program, encoding="utf-8")
    result = subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.returncode, result.stdout + result.stderr


def _codes(out: str) -> list[str]:
    return re.findall(r"\b(C[EW]\d{4})\b", out)


@needs_sushic
@pytest.mark.parametrize("lib_source, lib_kind, program, expected", [
    pytest.param(PRIVATE_STRUCT_LIB, "source", STRUCT_CLASH, "CE3011",
                 id="source-private-struct-vs-struct"),
    pytest.param(PRIVATE_STRUCT_LIB, "source", ENUM_CLASH, "CE3011",
                 id="source-private-struct-vs-enum"),
    pytest.param(PRIVATE_STRUCT_LIB, "binary", STRUCT_CLASH, "CE3011",
                 id="binary-private-struct-vs-struct"),
    pytest.param(PRIVATE_STRUCT_LIB, "binary", ENUM_CLASH, "CE3011",
                 id="binary-private-struct-vs-enum"),
    pytest.param(PRIVATE_TEMPLATE_LIB, "binary", GENERIC_CLASH, "CE3011",
                 id="binary-private-template-vs-template"),
    pytest.param(PRIVATE_TEMPLATE_LIB, "binary", STRUCT_CLASH, "CE3011",
                 id="binary-private-template-vs-struct"),
    pytest.param(PRIVATE_TEMPLATE_LIB, "binary", ENUM_CLASH, "CE3011",
                 id="binary-private-template-vs-enum"),
])
def test_refused_type_is_one_diagnostic(tmp_path, lib_source, lib_kind, program,
                                        expected):
    """The refusal at the consumer's declaration, and nothing about its own uses."""
    env = _build_lib(tmp_path, lib_source, lib_kind)
    code, out = _consume(tmp_path, env, program)

    assert code == 2, out
    assert _codes(out) == [expected], out
    assert "main.sushi:3:" in out, out
    if expected == "CE3011":
        assert "acclib" in out, out


@needs_sushic
@pytest.mark.parametrize("lib_source, lib_kind", [
    pytest.param(PRIVATE_STRUCT_LIB, "source", id="source"),
    pytest.param(PRIVATE_TEMPLATE_LIB, "binary", id="binary-template"),
])
def test_own_type_typo_still_speaks(tmp_path, lib_source, lib_kind):
    """The control: a type the consumer owns alone is measured as it always was."""
    env = _build_lib(tmp_path, lib_source, lib_kind)
    code, out = _consume(tmp_path, env, OWN_TYPE_TYPO)

    assert code == 2, out
    assert _codes(out) == ["CE2106"], out


PRIVATE_STRUCT_UNIT = """\
struct Acc:
    i32 total

public fn total() i32:
    let Acc acc = Acc(6)
    return Result.Ok(acc.total)
"""

ENUM_UNIT_CLASH = """\
use "a"

enum Acc:
    Empty

fn main() i32:
    let Acc mine = Acc.Empty
    println("{total().realise(0)}")
    return Result.Ok(0)
"""


@needs_sushic
def test_contest_does_not_depend_on_the_winner_kind(tmp_path):
    """An enum that lost its name to another unit's struct is contested as a struct too.

    CE0006 stands at the enum. The use of the name in the losing unit is not a use of
    the winner's private struct, so CE3005 must not follow it.
    """
    (tmp_path / "a.sushi").write_text(PRIVATE_STRUCT_UNIT, encoding="utf-8")
    (tmp_path / "main.sushi").write_text(ENUM_UNIT_CLASH, encoding="utf-8")
    result = subprocess.run([SUSHIC, "main.sushi", "-o", "out"], cwd=tmp_path,
                            capture_output=True, text=True,
                            env={**os.environ, "NO_COLOR": "1"})
    out = result.stdout + result.stderr

    assert result.returncode == 2, out
    codes = _codes(out)
    assert codes[0] == "CE0006", out
    assert "CE3005" not in codes, out
