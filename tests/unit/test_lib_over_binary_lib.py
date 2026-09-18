"""A compiled library may be built over a BINARY `.slib` dependency (#645).

A `--lib-kind source` build compiles no bitcode, so it never asks the back end for a
prototype and it always worked. A `binary` or `hybrid` build does compile bitcode, and
it reached the dispatcher with no declaration for the dependency's symbols -- the
`KeyError` that renders as CE0000. The cause was the hand-off: only the PROGRAM path
gave the code generator the resolver, the registry and the shipped perk
implementations, so the library path's declaration step was gated off.

The build is the whole rule here. A library records its dependency as an external
symbol and does not link it; the consumer states each library for itself
(`docs/libraries.md`, limitation 1), which is what the end-to-end case below spells.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, SUSHIC_AVAILABLE

# The dependency: one function, one perk and its concrete implementation. Both arms of
# the declaration step read this library -- the registry declares `a_one`, and the
# template arm declares `i32_doubled` for the perk implementation.
DEPENDENCY = """\
public fn a_one() i32:
    return Result.Ok(1)

public perk Doubler:
    fn doubled() i32

extend i32 with Doubler:
    fn doubled() i32:
        return self * 2
"""

# The middle library: it CALLS the dependency's function and, in the second position,
# a method the dependency's perk implementation provides.
MIDDLE_CALLS_FUNCTION = """\
use <lib/liba>

public fn c_two() i32:
    return Result.Ok(a_one()?? + 1)
"""

MIDDLE_CALLS_PERK_METHOD = """\
use <lib/liba>

public fn quadruple(i32 n) i32:
    return Result.Ok(n.doubled().doubled())
"""

# The program: it states BOTH libraries, because a library dependency is not
# transitive.
PROGRAM = """\
use <lib/liba>
use <lib/libc>

fn main() i32:
    println(c_two().realise(0))
    return Result.Ok(0)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _libs_dir(tmp_path: Path) -> Path:
    libs = tmp_path / "libs"
    libs.mkdir(exist_ok=True)
    return libs


def _env(tmp_path: Path) -> dict:
    return {**os.environ, "SUSHI_LIB_PATH": str(_libs_dir(tmp_path)), "NO_COLOR": "1"}


def _build_lib(tmp_path: Path, name: str, source: str, kind: str):
    """Build one single-unit library of the given kind under tmp_path/libs."""
    if not SUSHIC_AVAILABLE:
        pytest.skip(f"no compiler driver at {SUSHIC}")
    _write(tmp_path / f"{name}.sushi", source)
    return subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(tmp_path / f"{name}.sushi"), "-o", str(_libs_dir(tmp_path) / f"{name}.slib")],
        cwd=tmp_path, capture_output=True, text=True, env=_env(tmp_path))


@pytest.mark.parametrize("kind", ["binary", "hybrid", "source"])
def test_a_library_builds_over_a_binary_dependency(tmp_path, kind):
    """Every kind of consuming build, over one binary dependency."""
    assert _build_lib(tmp_path, "liba", DEPENDENCY, "binary").returncode == 0

    built = _build_lib(tmp_path, "libc", MIDDLE_CALLS_FUNCTION, kind)

    assert built.returncode == 0, built.stdout + built.stderr
    assert "CE0000" not in built.stdout + built.stderr
    assert (_libs_dir(tmp_path) / "libc.slib").is_file()


@pytest.mark.parametrize("kind", ["binary", "hybrid"])
def test_a_library_builds_over_a_dependency_perk_implementation(tmp_path, kind):
    """The second position behind the same gate: a shipped perk implementation."""
    assert _build_lib(tmp_path, "liba", DEPENDENCY, "binary").returncode == 0

    built = _build_lib(tmp_path, "libc", MIDDLE_CALLS_PERK_METHOD, kind)

    assert built.returncode == 0, built.stdout + built.stderr
    assert "CE0000" not in built.stdout + built.stderr


def test_a_program_runs_over_a_binary_library_built_over_a_binary_library(tmp_path):
    """End to end: the program states both libraries and calls through the middle one."""
    assert _build_lib(tmp_path, "liba", DEPENDENCY, "binary").returncode == 0
    assert _build_lib(tmp_path, "libc", MIDDLE_CALLS_FUNCTION, "binary").returncode == 0

    project = tmp_path / "prog"
    _write(project / "main.sushi", PROGRAM)
    built = subprocess.run([SUSHIC, "main.sushi", "-o", "out"], cwd=project,
                           capture_output=True, text=True, env=_env(tmp_path))
    assert built.returncode == 0, built.stdout + built.stderr

    ran = subprocess.run([str(project / "out")], cwd=project,
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout == "2\n"
