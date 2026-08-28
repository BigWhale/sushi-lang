"""A constant is private by default, and a contested one hears exactly one diagnostic.

Ruling 1 of `docs/design/visibility.md`. Three shapes, one diagnostic each:

  both public   -> CE3003, the ambiguous export, now with a note at each declaration
  both private  -> nothing. Each declaration takes its own `<unit>$<name>` global, so the
                   two coexist (`docs/design/unit-namespaces.md` section 9). CE0105 keeps
                   the same name twice inside ONE unit.
  a library's   -> CE0105 still. D1 asked whether a private library constant deserves
                   CE3011 like a function does; it does not, because CE0105 already names
                   the library's file in its note and is the only diagnostic for the
                   condition. A library clash is not this epic's to lift.
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
    project = tmp_path / "prog"
    for name, source in units.items():
        _write(project / name, source)
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True,
        env={**os.environ, "NO_COLOR": "1"},
    )
    return result.stdout + result.stderr


def _units(marker: str) -> dict[str, str]:
    return {
        "helper.sushi": f"""\
{marker}const i32 LIMIT = 5

public fn helper_limit() i32:
    return Result.Ok(LIMIT)
""",
        "main.sushi": f"""\
use "helper"

{marker}const i32 LIMIT = 7

fn main() i32:
    println("{{LIMIT}} {{helper_limit().realise(0)}}")
    return Result.Ok(0)
""",
    }


@needs_sushic
def test_two_public_constants_are_an_ambiguous_export(tmp_path):
    out = _compile_units(tmp_path, _units("public "))
    assert "CE3003" in out, out
    assert out.count("exported here") == 2, out
    assert "CE0105" not in out, out


@needs_sushic
def test_two_private_constants_coexist(tmp_path):
    """Each takes its own `<unit>$<name>` global, so neither has to lose.

    That the two values actually read back is `tests/namespaces/mangling/`; this asserts
    that no diagnostic is spent on them (`docs/design/unit-namespaces.md` section 9).
    """
    out = _compile_units(tmp_path, _units(""))
    assert "CE0105" not in out, out
    assert "CE3003" not in out, out


@needs_sushic
def test_a_library_private_constant_clash_is_the_same_duplicate(tmp_path):
    libs = tmp_path / "libs"
    libs.mkdir()
    lib_src = tmp_path / "climb.sushi"
    _write(lib_src, """\
const i32 LIMIT = 5

public fn lib_limit() i32:
    return Result.Ok(LIMIT)
""")
    built = subprocess.run(
        ["sushic", "--lib", "--lib-kind", "source", "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs / "climb.slib")],
        cwd=tmp_path, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr

    project = tmp_path / "prog"
    _write(project / "main.sushi", """\
use <lib/climb>

const i32 LIMIT = 7

fn main() i32:
    println("{LIMIT} {lib_limit().realise(0)}")
    return Result.Ok(0)
""")
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"], cwd=project,
        capture_output=True, text=True,
        env={**os.environ, "SUSHI_LIB_PATH": str(libs), "NO_COLOR": "1"})
    out = result.stdout + result.stderr
    assert "CE0105" in out, out
    assert "CE3011" not in out, out
    assert "CE3003" not in out, out


@needs_sushic
def test_a_private_constant_is_not_an_export(tmp_path):
    """The unit summary counts exports, and a private constant is not one."""
    out = _compile_units(tmp_path, {
        "helper.sushi": """\
const i32 QUIET = 5

public const i32 LOUD = 6
""",
        "main.sushi": """\
use "helper"

fn main() i32:
    println("{LOUD}")
    return Result.Ok(0)
""",
    })
    assert "helper (1 public symbols)" in out, out
