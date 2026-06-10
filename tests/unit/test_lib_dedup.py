"""Cross-library generic dedup guarantee (P2-5 Phase 3 / C2).

Two consumer *units* instantiating the same library generic at the same type
must produce exactly ONE monomorphized definition in the build. Instances
centralize in ``compilation_order[0]`` and dedupe via ``func_cache``
(``semantics/generics/monomorphize/functions.py``); until now that guarantee
was only ever verified by hand with ``nm``. This test regression-protects it:
it fails if a duplicate definition is ever emitted.

Drives the real compiler (``sushic``): builds a ``.slib`` exporting a
constrained generic, then a two-unit consumer (which forces the incremental
per-unit ``.o`` path), and counts definitions of the mangled instance across
every object file in the build cache.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sushi_lang.semantics.generics.name_mangling import mangle_function_name


LIB_SOURCE = """\
perk Ord:
    fn gt(i32 other) bool

public fn max_of<T: Ord>(T a, T b) T:
    if (a > b):
        return Result.Ok(a)
    return Result.Ok(b)
"""

UTIL_SOURCE = """\
use <lib/mathlib>

public fn pick_util(i32 a, i32 b) i32:
    let i32 m = max_of(a, b).realise(0)
    return Result.Ok(m)
"""

MAIN_SOURCE = """\
use "helpers/util"
use <lib/mathlib>

extend i32 with Ord:
    fn gt(i32 other) bool:
        return self > other

fn main() i32:
    let i32 a = pick_util(3, 7).realise(0)
    let i32 b = max_of(10, 4).realise(0)
    println(a)
    println(b)
    return Result.Ok(0)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Build the library and the two-unit consumer; return (project_dir, env)."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir()
    lib_src = tmp_path / "mathlib.sushi"
    _write(lib_src, LIB_SOURCE)

    build = subprocess.run(
        ["sushic", "--lib", str(lib_src), "-o", str(libs_dir / "mathlib.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, f"Library build failed:\n{build.stderr}"

    project = tmp_path / "consumer"
    _write(project / "helpers" / "util.sushi", UTIL_SOURCE)
    _write(project / "main.sushi", MAIN_SOURCE)

    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}
    compile_result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert compile_result.returncode == 0, f"Consumer build failed:\n{compile_result.stderr}"

    # Two-unit consumer must take the incremental (per-unit .o) path.
    assert "[rebuilt]" in compile_result.stdout, (
        "Expected the incremental per-unit path (two-unit consumer); "
        f"got:\n{compile_result.stdout}"
    )
    return project, env


def _defined_symbol_count(obj_path: Path, symbol: str) -> int:
    """Count *definitions* of ``symbol`` in an object file via ``nm``.

    Any symbol-type letter other than ``U`` (undefined) counts as a
    definition. Darwin prefixes symbols with an underscore, so the match
    tolerates one leading ``_``.
    """
    result = subprocess.run(
        ["nm", str(obj_path)], capture_output=True, text=True,
    )
    assert result.returncode == 0, f"nm failed on {obj_path}:\n{result.stderr}"
    pattern = re.compile(rf"^_?{re.escape(symbol)}$")
    count = 0
    for line in result.stdout.splitlines():
        parts = line.split()
        # nm lines: [address] <type-letter> <name>; undefined have no address.
        if len(parts) < 2:
            continue
        type_letter, name = parts[-2], parts[-1]
        if pattern.match(name) and type_letter.upper() != "U":
            count += 1
    return count


@pytest.mark.skipif(shutil.which("nm") is None, reason="nm not available")
@pytest.mark.skipif(shutil.which("sushic") is None, reason="sushic not on PATH")
def test_single_monomorphized_definition_across_units(tmp_path):
    """Both consumer units call max_of at i32; exactly one max_of__i32 is defined."""
    project, _ = _build_project(tmp_path)

    mangled = mangle_function_name("max_of", ["i32"])
    cache_dir = project / "__sushi_cache__"
    obj_files = sorted(cache_dir.rglob("*.o"))
    assert obj_files, f"No object files found under {cache_dir}"

    per_file = {
        str(o.relative_to(cache_dir)): _defined_symbol_count(o, mangled)
        for o in obj_files
    }
    total = sum(per_file.values())
    assert total == 1, (
        f"Expected exactly one definition of {mangled!r} across the build, "
        f"found {total}: {per_file}"
    )


@pytest.mark.skipif(shutil.which("sushic") is None, reason="sushic not on PATH")
def test_two_unit_consumer_runs_correctly(tmp_path):
    """Anchor for the dedup assertion: the two-unit build links and runs."""
    project, _ = _build_project(tmp_path)

    out = subprocess.run([str(project / "out")], capture_output=True, text=True)
    assert out.returncode == 0
    lines = out.stdout.split()
    assert lines == ["7", "10"], f"Unexpected program output: {out.stdout!r}"
