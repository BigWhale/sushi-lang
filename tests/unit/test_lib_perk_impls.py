"""Cross-library concrete perk-impl shipping (P2-5 Phase 2 / C4a).

Covers the incremental (multi-unit consumer) link path the single-file golden
harness cannot reach: there the whole library bitcode becomes one ``.o``
linked by plain ``cc``, so a consumer's local override of a shipped impl is a
duplicate-symbol hard error unless the library's perk-impl symbols carry weak
linkage. These tests gate that linkage and the end-to-end behavior.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


LIB_SOURCE = """\
perk Doubler:
    fn doubled() i32

extend i32 with Doubler:
    fn doubled() i32:
        return self * 2

public fn pick_bigger<T: Doubler>(T a, T b) T:
    if (a.doubled() > b.doubled()):
        return Result.Ok(a)
    return Result.Ok(b)
"""

UTIL_SOURCE = """\
use <lib/impllib>

public fn pick_util(i32 a, i32 b) i32:
    let i32 m = pick_bigger(a, b).realise(0)
    return Result.Ok(m)
"""

MAIN_SHIPPED = """\
use "helpers/util"
use <lib/impllib>

fn main() i32:
    let i32 a = pick_util(3, 5).realise(99)
    println(a)
    return Result.Ok(0)
"""

MAIN_OVERRIDE = """\
use "helpers/util"
use <lib/impllib>

extend i32 with Doubler:
    fn doubled() i32:
        return 0 - self

fn main() i32:
    let i32 a = pick_util(3, 5).realise(99)
    println(a)
    return Result.Ok(0)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path) -> dict[str, str]:
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / "impllib.sushi"
    _write(lib_src, LIB_SOURCE)
    build = subprocess.run(
        ["sushic", "--lib", str(lib_src), "-o", str(libs_dir / "impllib.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, f"Library build failed:\n{build.stderr}"
    return {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}


def _build_consumer(tmp_path: Path, main_source: str, env: dict[str, str]) -> Path:
    """Two-unit consumer -> incremental per-unit .o path."""
    project = tmp_path / "consumer"
    _write(project / "helpers" / "util.sushi", UTIL_SOURCE)
    _write(project / "main.sushi", main_source)
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"Consumer build failed:\n{result.stderr}"
    assert "[rebuilt]" in result.stdout, (
        f"Expected the incremental per-unit path; got:\n{result.stdout}"
    )
    return project


def _run(project: Path) -> str:
    out = subprocess.run([str(project / "out")], capture_output=True, text=True)
    assert out.returncode == 0, f"Program failed: {out.stderr}"
    return out.stdout.strip()


@pytest.mark.skipif(shutil.which("sushic") is None, reason="sushic not on PATH")
def test_shipped_impl_links_and_runs_multi_unit(tmp_path):
    """No consumer extend: the library's i32_doubled is registered, declared,
    and linked from the library .o across two consumer units."""
    env = _build_lib(tmp_path)
    project = _build_consumer(tmp_path, MAIN_SHIPPED, env)
    assert _run(project) == "5"


@pytest.mark.skipif(shutil.which("sushic") is None, reason="sushic not on PATH")
def test_local_override_links_and_wins_multi_unit(tmp_path):
    """Local extend + shipped impl: must LINK (the library symbol is weak; a
    duplicate-symbol cc error here means the linkage regressed) and the local
    negating impl must win at runtime (picks 3, not 5)."""
    env = _build_lib(tmp_path)
    project = _build_consumer(tmp_path, MAIN_OVERRIDE, env)
    assert _run(project) == "3"


def _is_weak_definition(obj: Path, symbol: str) -> bool | None:
    """Return True/False for a found definition's weakness, None if absent.

    Mach-O's classic ``nm`` letters show weak definitions as plain ``T``, so
    on darwin the ``nm -m`` long form is consulted ("weak external <sym>");
    on ELF the standard W/w/V/v letters apply.
    """
    if sys.platform == "darwin":
        nm_out = subprocess.run(["nm", "-m", str(obj)], capture_output=True, text=True)
        assert nm_out.returncode == 0, f"nm -m failed on {obj}:\n{nm_out.stderr}"
        for line in nm_out.stdout.splitlines():
            if re.search(rf"\b_?{re.escape(symbol)}$", line) and "__text" in line:
                return "weak external" in line
        return None

    nm_out = subprocess.run(["nm", str(obj)], capture_output=True, text=True)
    assert nm_out.returncode == 0, f"nm failed on {obj}:\n{nm_out.stderr}"
    pattern = re.compile(rf"^_?{re.escape(symbol)}$")
    for line in nm_out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        type_letter, name = parts[-2], parts[-1]
        if pattern.match(name) and type_letter.upper() != "U":
            return type_letter in ("W", "w", "V", "v")
    return None


@pytest.mark.skipif(shutil.which("nm") is None, reason="nm not available")
@pytest.mark.skipif(shutil.which("sushic") is None, reason="sushic not on PATH")
def test_shipped_impl_symbol_is_weak_in_library_object(tmp_path):
    """i32_doubled must be present in the library-derived .o and weak.

    Presence gates against module-level optimization dropping the unreferenced
    definition (the reason for weak_odr over linkonce_odr); weakness gates the
    override behavior of the previous test.
    """
    env = _build_lib(tmp_path)
    project = _build_consumer(tmp_path, MAIN_SHIPPED, env)

    lib_objs = sorted((project / "__sushi_cache__").rglob("lib*.o"))
    assert lib_objs, "No library-derived object file found in the cache"

    verdicts = {o.name: _is_weak_definition(o, "i32_doubled") for o in lib_objs}
    found = {name: v for name, v in verdicts.items() if v is not None}
    assert found, f"i32_doubled not defined in any library-derived .o: {verdicts}"
    assert all(found.values()), (
        f"i32_doubled must be a weak definition in the library .o, got: {found}"
    )
