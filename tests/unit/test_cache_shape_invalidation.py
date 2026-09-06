"""A dependency's declaration SHAPE must invalidate the units that read it (#593).

`DEP_SYMBOLS` used to fold in a dependency's public function and constant SIGNATURES and
nothing else. A struct's field list, an enum's variant order, a constant's VALUE and a
source library's units were all outside the digest, so a dependent kept an object file
built against the old shape. The build then printed a wrong answer with no diagnostic,
and `--clean-cache` was the only way back.

The gate every test here reads: a cached build and a `--no-incremental` build of the same
tree must produce the same program.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, SUSHIC_AVAILABLE


pytestmark = pytest.mark.skipif(not SUSHIC_AVAILABLE,
                                reason="no compiler driver in this checkout")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _sushic(args, cwd, env_extra=None):
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run([SUSHIC, *args], cwd=cwd, capture_output=True,
                          text=True, env=env)


def _build(project: Path, out: str, flags=(), entry="main.sushi", env_extra=None):
    r = _sushic([entry, "-o", out, *flags], cwd=project, env_extra=env_extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def _run(project: Path, out: str) -> str:
    r = subprocess.run([str(project / out)], capture_output=True, text=True)
    return r.stdout


def _cached(stdout: str) -> set[str]:
    return {line.strip().split()[0] for line in stdout.splitlines() if "[cached]" in line}


def _cached_matches_clean(project: Path, entry="main.sushi", env_extra=None) -> None:
    """Build twice over the same tree: incrementally, then from scratch."""
    incr = _build(project, "incremental", entry=entry, env_extra=env_extra)
    clean = _build(project, "clean", flags=("--no-incremental",), entry=entry,
                   env_extra=env_extra)
    got = _run(project, "incremental")
    want = _run(project, "clean")
    assert got == want, (
        f"the cached build printed {got!r} where a full build prints {want!r}.\n"
        f"cached units: {sorted(_cached(incr.stdout))}\n{incr.stdout}{clean.stdout}"
    )


# --- A plain second unit -----------------------------------------------------

def test_a_reordered_struct_field_invalidates_the_dependent(tmp_path):
    """The layout moved under an untouched dependent: `b.w` now reads the other slot."""
    _write(tmp_path / "shapes.sushi", "public struct Box:\n    i32 w\n    i32 h\n")
    _write(tmp_path / "main.sushi", """\
use "shapes"

fn main() i32:
    let Box b = Box(3, 4)
    println("{b.w} {b.h}")
    return Result.Ok(0)
""")
    _build(tmp_path, "warm")
    assert _run(tmp_path, "warm") == "3 4\n"

    _write(tmp_path / "shapes.sushi", "public struct Box:\n    i32 h\n    i32 w\n")
    _cached_matches_clean(tmp_path)
    assert _run(tmp_path, "incremental") == "4 3\n"


def test_a_widened_struct_field_invalidates_the_dependent(tmp_path):
    """A field's TYPE moves the offsets after it, and the value crosses the boundary."""
    _write(tmp_path / "shapes.sushi", """\
public struct Box:
    i32 w
    i32 h

public fn make() Box:
    return Result.Ok(Box(3, 4))
""")
    _write(tmp_path / "main.sushi", """\
use "shapes"

fn main() i32:
    match make():
        Result.Ok(b) -> println("{b.w} {b.h}")
        Result.Err(_) -> println("err")
    return Result.Ok(0)
""")
    _build(tmp_path, "warm")
    assert _run(tmp_path, "warm") == "3 4\n"

    _write(tmp_path / "shapes.sushi", """\
public struct Box:
    i64 w
    i32 h

public fn make() Box:
    return Result.Ok(Box(3, 4))
""")
    _cached_matches_clean(tmp_path)
    assert _run(tmp_path, "incremental") == "3 4\n"


def test_a_reordered_enum_variant_invalidates_the_dependent(tmp_path):
    """A variant's TAG is its position, and the dependent bakes the tag into its match."""
    _write(tmp_path / "shapes.sushi", """\
public enum Sign:
    Plus
    Minus

public fn plus() Sign:
    return Result.Ok(Sign.Plus)
""")
    _write(tmp_path / "main.sushi", """\
use "shapes"

fn main() i32:
    match plus():
        Result.Ok(s) -> report(s)
        Result.Err(_) -> println("err")
    return Result.Ok(0)

fn report(Sign s) ~:
    match s:
        Sign.Plus -> println("plus")
        Sign.Minus -> println("minus")
    return Result.Ok(~)
""")
    _build(tmp_path, "warm")
    assert _run(tmp_path, "warm") == "plus\n"

    _write(tmp_path / "shapes.sushi", """\
public enum Sign:
    Minus
    Plus

public fn plus() Sign:
    return Result.Ok(Sign.Plus)
""")
    _cached_matches_clean(tmp_path)
    assert _run(tmp_path, "incremental") == "plus\n"


def test_a_constant_value_change_invalidates_the_dependent(tmp_path):
    """A constant is FOLDED into the dependent, so its value is part of the interface."""
    _write(tmp_path / "cfg.sushi", "public const i32 LIMIT = 10\n")
    _write(tmp_path / "main.sushi", """\
use "cfg"

fn main() i32:
    println("{LIMIT}")
    return Result.Ok(0)
""")
    _build(tmp_path, "warm")
    assert _run(tmp_path, "warm") == "10\n"

    _write(tmp_path / "cfg.sushi", "public const i32 LIMIT = 20\n")
    _cached_matches_clean(tmp_path)
    assert _run(tmp_path, "incremental") == "20\n"


# --- The chain: a shape reached through a `public use` ------------------------

def test_a_shape_two_units_away_invalidates_the_dependent(tmp_path):
    """`public use` re-exports, so a unit names a type its own import does not declare."""
    _write(tmp_path / "a.sushi", "public struct Box:\n    i32 w\n    i32 h\n")
    _write(tmp_path / "b.sushi", 'public use "a"\n')
    _write(tmp_path / "main.sushi", """\
use "b"

fn main() i32:
    let Box b = Box(3, 4)
    println("{b.w} {b.h}")
    return Result.Ok(0)
""")
    _build(tmp_path, "warm")
    assert _run(tmp_path, "warm") == "3 4\n"

    _write(tmp_path / "a.sushi", "public struct Box:\n    i32 h\n    i32 w\n")
    _cached_matches_clean(tmp_path)
    assert _run(tmp_path, "incremental") == "4 3\n"


# --- A source `.slib` --------------------------------------------------------

def _build_source_lib(project: Path, text: str) -> Path:
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    _write(project / "boxlib.sushi", text)
    r = _sushic(["boxlib.sushi", "--lib", "--lib-version", "1.0.0",
                 "-o", str(lib_dir / "boxlib.slib")], cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    return lib_dir


LIB_V1 = """\
public struct Box:
    i32 w
    i32 h

public fn make() Box:
    return Result.Ok(Box(3, 4))
"""

LIB_V2 = """\
public struct Box:
    i64 tag
    i32 w
    i32 h

public fn make() Box:
    return Result.Ok(Box(99, 3, 4))
"""

APP = """\
use <lib/boxlib>

fn main() i32:
    match make():
        Result.Ok(b) -> println("{b.w} {b.h}")
        Result.Err(_) -> println("err")
    return Result.Ok(0)
"""


def test_a_source_library_shape_change_invalidates_the_consumer(tmp_path):
    """An injected library unit is a dependency edge, not just an ordering edge."""
    lib_dir = _build_source_lib(tmp_path, LIB_V1)
    _write(tmp_path / "app.sushi", APP)
    env = {"SUSHI_LIB_PATH": str(lib_dir)}

    _build(tmp_path, "warm", entry="app.sushi", env_extra=env)
    assert _run(tmp_path, "warm") == "3 4\n"

    _build_source_lib(tmp_path, LIB_V2)
    _cached_matches_clean(tmp_path, entry="app.sushi", env_extra=env)
    assert _run(tmp_path, "incremental") == "3 4\n"
