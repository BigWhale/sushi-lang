"""A consumer cannot declare a type name a BINARY library exports (#739).

A TYPE is one name for the whole program (Known Limitation 13). A SOURCE library's
units are ordinary compilation units, so the collect pass answered the clash already --
CE0004 for a struct, CE2046 for an enum. A BINARY library arrives as a manifest, and
the `libraries` step registered its types by updating the live struct and enum tables:
the consumer's own declaration was overwritten in place, with no word said. What the
consumer then heard was its own code measured against the LIBRARY's shape -- CE2027
about a field count it never spelled, CE2106 about a field it did write, CE2045 about
its own variant.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is ABSENT, so the
absence of that cascade is pinned here, on the whole rendered output.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic


TYPE_LIB = """\
public struct Crate:
    i32 weight
    i32 count

public enum Mood:
    Calm
    Cross

public fn describe(Crate c) i32:
    return Result.Ok(c.weight + c.count)
"""

STRUCT_CLASH = """\
use <lib/typelib>

struct Crate:
    i32 mine

fn main() i32:
    let Crate c = Crate(1)
    println("{c.mine}")
    return Result.Ok(0)
"""

ENUM_CLASH = """\
use <lib/typelib>

enum Mood:
    Sleepy

fn main() i32:
    let Mood m = Mood.Sleepy
    match m:
        Mood.Sleepy -> println("sleepy")
    return Result.Ok(0)
"""

NO_CLASH = """\
use <lib/typelib>

fn main() i32:
    let Crate c = Crate(3, 4)
    println("{describe(c).realise(0)}")
    return Result.Ok(0)
"""


def _build_lib(tmp_path: Path, kind: str) -> dict:
    """Build `TYPE_LIB` as a `.slib` of `kind` and return the consumer's environment."""
    libs_dir = tmp_path / f"libs_{kind}"
    libs_dir.mkdir(parents=True, exist_ok=True)
    lib_src = tmp_path / f"typelib_{kind}.sushi"
    lib_src.write_text(TYPE_LIB, encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / "typelib.slib")],
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
    return re.findall(r"\b(CE\d{4})\b", out)


# --- the binary path: the clash is refused, and nothing else is said ----------------

@needs_sushic
def test_binary_library_struct_name_is_refused(tmp_path):
    """One CE3011 at the consumer's declaration, and no word about the library shape."""
    env = _build_lib(tmp_path, "binary")
    code, out = _consume(tmp_path, env, STRUCT_CLASH, "struct_clash")

    assert code == 2, out
    assert _codes(out).count("CE3011") == 1, out
    assert "typelib" in out, out
    # The cascade the fault used to be: the consumer's own lines measured against the
    # library's two-field `Crate`.
    assert "CE2027" not in out, out
    assert "CE2106" not in out, out


@needs_sushic
def test_binary_library_enum_name_is_refused(tmp_path):
    """The enum half: one CE3011, and no complaint about the consumer's own variant."""
    env = _build_lib(tmp_path, "binary")
    code, out = _consume(tmp_path, env, ENUM_CLASH, "enum_clash")

    assert code == 2, out
    assert _codes(out).count("CE3011") == 1, out
    assert "CE2045" not in out, out
    assert "CE2040" not in out, out


@needs_sushic
def test_binary_library_type_still_reaches_a_consumer(tmp_path):
    """The control: a consumer that declares nothing still gets the library's types."""
    env = _build_lib(tmp_path, "binary")
    code, out = _consume(tmp_path, env, NO_CLASH, "no_clash")

    assert code == 0, out
    assert "CE" not in out, out


# --- the source path is unchanged ---------------------------------------------------

@needs_sushic
def test_source_library_struct_name_stays_the_plain_duplicate(tmp_path):
    """A source library's declaration is visible, so the duplicate reads as one."""
    env = _build_lib(tmp_path, "source")
    code, out = _consume(tmp_path, env, STRUCT_CLASH, "struct_clash_src")

    assert code == 2, out
    assert "CE0004" in out, out
    assert "CE3011" not in out, out


@needs_sushic
def test_source_library_enum_name_stays_the_plain_duplicate(tmp_path):
    """The enum twin of the same control: CE2046, once, with its note."""
    env = _build_lib(tmp_path, "source")
    code, out = _consume(tmp_path, env, ENUM_CLASH, "enum_clash_src")

    assert code == 2, out
    assert _codes(out).count("CE2046") == 1, out
    assert "CE3011" not in out, out
