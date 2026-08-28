"""One diagnostic per contested name, and never one aimed at the wrong unit (D2).

A flat namespace means two units cannot declare the same TYPE. The symbol table keeps
the FIRST declaration, so every later one is a loss, and the unit that lost hears why:
CE0004 for a struct, CE2046 for an enum, and CE3011 when the winner is a library's
PRIVATE declaration, which the consumer cannot even see. A function and a constant left
this list: each takes its own `<unit>$<name>` symbol, so the two coexist
(`docs/design/unit-namespaces.md` section 9). What a loser must NOT
then hear is a rule measuring its own code against a declaration it did not write --
CE3005 says "you may not call this private", CE2027 says the struct has the wrong number
of fields, CE2045 names a variant it did write against an enum it did not, and CE2060 says
a generic cannot be inferred. Every one of those names the winner's declaration.

A library's PUBLIC name is a different rule and stays one: the program's own declaration
takes priority and warns (CW3002), which `tests/libs/test_warn_lib_override.sushi` and
`tests/unit/test_library_shadow_warning.py` are the contract for.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _compile_units(tmp_path: Path, units: dict[str, str]) -> str:
    """Compile a multi-unit program. `units` maps file name to source."""
    project = tmp_path / "prog"
    for name, source in units.items():
        _write(project / name, source)
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True,
        env={**os.environ, "NO_COLOR": "1"},
    )
    return result.stdout + result.stderr


def _build_source_lib(tmp_path: Path, source: str, name: str) -> tuple[str, dict]:
    """Build `source` as a SOURCE .slib. Returns (output, env pointing at it)."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    result = subprocess.run(
        ["sushic", "--lib", "--lib-kind", "source", "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / f"{name}.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout, {**os.environ, "SUSHI_LIB_PATH": str(libs_dir),
                           "NO_COLOR": "1"}


def _consume(tmp_path: Path, env: dict, program: str) -> str:
    project = tmp_path / "prog"
    _write(project / "main.sushi", program)
    result = subprocess.run(["sushic", "main.sushi", "-o", "out"],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.stdout + result.stderr


# --- (a) two ordinary units -------------------------------------------------------


@needs_sushic
def test_two_units_may_each_declare_one_function(tmp_path):
    """A function is no longer a contested name: each unit takes its own symbol.

    There is nothing to lose, so there is no cascade to hear either. That the two
    bodies actually run is `tests/namespaces/mangling/`; this asserts the silence.
    """
    out = _compile_units(tmp_path, {
        "helper.sushi": """\
fn scale(i32 n) i32:
    return Result.Ok(n * 2)

fn helper_main() i32:
    return Result.Ok(scale(1)??)
""",
        "main.sushi": """\
use "helper"

fn scale(i32 n) i32:
    return Result.Ok(n * 3)

fn main() i32:
    println("{scale(2).realise(0)}")
    return Result.Ok(0)
""",
    })
    assert "CE0101" not in out, out
    assert "CE3005" not in out, out


@needs_sushic
def test_two_units_declaring_one_struct_hear_it_once(tmp_path):
    out = _compile_units(tmp_path, {
        "helper.sushi": """\
struct Point:
    i32 x
    i32 y

fn helper_sum() i32:
    let Point p = Point(1, 2)
    return Result.Ok(p.x + p.y)
""",
        "main.sushi": """\
use "helper"

struct Point:
    i32 tag

fn main() i32:
    let Point p = Point(7)
    println("{p.tag}")
    return Result.Ok(0)
""",
    })
    assert "CE0004" in out, out
    # The loser's own construction is measured against the winner's field count.
    assert "CE2027" not in out, out


# --- (b) and (c) a source library ------------------------------------------------


@needs_sushic
def test_a_source_library_private_function_clash_is_refused(tmp_path):
    _out, env = _build_source_lib(tmp_path, """\
fn scale(i32 n) i32:
    return Result.Ok(n * 2)

public fn twice(i32 n) i32:
    return Result.Ok(scale(n)??)
""", "fnlib")
    out = _consume(tmp_path, env, """\
use <lib/fnlib>

fn scale(i32 n) i32:
    return Result.Ok(n * 3)

fn main() i32:
    println("{scale(2).realise(0)}")
    return Result.Ok(0)
""")
    assert "CE3011" in out, out
    assert "CE3005" not in out, out


@needs_sushic
def test_a_source_library_struct_clash_does_not_cascade(tmp_path):
    _out, env = _build_source_lib(tmp_path, """\
struct Point:
    i32 x
    i32 y

public fn origin_sum() i32:
    let Point p = Point(0, 0)
    return Result.Ok(p.x + p.y)
""", "geolib")
    out = _consume(tmp_path, env, """\
use <lib/geolib>

struct Point:
    i32 tag

fn main() i32:
    let Point p = Point(7)
    println("{p.tag}")
    return Result.Ok(0)
""")
    # The library's struct carries no marker, so it is private and the consumer may not
    # take the name at all (CE3011). A library type the library DOES export stays the
    # plain duplicate, CE0004: type identity is nominal, so one name is one shape and the
    # consumer cannot have its own.
    assert "CE3011" in out, out
    assert "CE2027" not in out, out


@needs_sushic
def test_a_source_library_enum_clash_does_not_cascade(tmp_path):
    _out, env = _build_source_lib(tmp_path, """\
enum Mood:
    Calm
    Cross

public fn is_calm() bool:
    let Mood m = Mood.Calm
    match m:
        Mood.Calm -> return Result.Ok(true)
        Mood.Cross -> return Result.Ok(false)
""", "moodlib")
    out = _consume(tmp_path, env, """\
use <lib/moodlib>

enum Mood:
    Sleepy

fn main() i32:
    let Mood m = Mood.Sleepy
    match m:
        Mood.Sleepy -> println("sleepy")
    return Result.Ok(0)
""")
    # As with the struct: the library's enum is private, so CE3011 refuses the name.
    # CE2045/CE2040 are the cascade -- a variant the user did write, against an enum they
    # did not, and an exhaustiveness they cannot satisfy.
    assert "CE3011" in out, out
    assert "CE2045" not in out, out
    assert "CE2040" not in out, out


# --- the bundled stdlib module ----------------------------------------------------


@needs_sushic
def test_a_bundled_module_public_name_may_be_overridden(tmp_path):
    """`iter` exports a generic `map`; a program's own concrete `map` is what it calls.

    This is the use case the removed shadow branch was written for, and it did not work:
    the branch dropped `iter`'s entry and registered no replacement, so the generic came
    back and the call arrived as CE2060 about a function the user did not write.
    """
    out = _compile_units(tmp_path, {
        "main.sushi": """\
use <collections/iter>

fn map(i32 n) i32:
    return Result.Ok(n + 1)

fn main() i32:
    println("{map(2).realise(0)}")
    return Result.Ok(0)
""",
    })
    assert "CE2060" not in out, out
    assert "error" not in out, out


# --- the control: a name nothing else declares -----------------------------------


@needs_sushic
def test_a_library_public_name_the_consumer_does_not_redeclare_still_works(tmp_path):
    _out, env = _build_source_lib(tmp_path, """\
fn scale(i32 n) i32:
    return Result.Ok(n * 2)

public fn twice(i32 n) i32:
    return Result.Ok(scale(n)??)
""", "oklib")
    out = _consume(tmp_path, env, """\
use <lib/oklib>

fn main() i32:
    println("{twice(2).realise(0)}")
    return Result.Ok(0)
""")
    assert "CE3011" not in out, out
    assert "error" not in out, out
