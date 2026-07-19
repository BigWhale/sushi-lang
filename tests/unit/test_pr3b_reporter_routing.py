"""PR3b: diagnostic-shaped prints routed through the Reporter.

These were `print()`s masquerading as diagnostics -- no code, no snippet, sometimes
hand-indented to fake a note under a real diagnostic. Routing them through the reporter
gives them the same shape (code, location, help) as every other diagnostic.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_HAS_SUSHIC = shutil.which("sushic") is not None


@pytest.mark.skipif(not _HAS_SUSHIC, reason="sushic not on PATH")
def test_generic_struct_constructor_hint_is_a_help_line(tmp_path):
    """The CE2008 generic-struct hint is a real `help:` line, not a hand-indented print.

    It used to be `print("      Generic struct constructors ...")` faking a note under
    the CE2008 it followed. It is now attached to the diagnostic as help.
    """
    src = tmp_path / "main.sushi"
    src.write_text(
        "struct Pair@(T, U):\n"
        "    T first\n"
        "    U second\n\n"
        "fn take(i32 x) i32:\n"
        "    return Result.Ok(x)\n\n"
        "fn main() i32:\n"
        "    let i32 y = take(Pair(1, 2)).realise(0)\n"
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["sushic", str(src), "-o", str(tmp_path / "out")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "CE2008" in combined
    # The hint now renders as a help line...
    assert "= help:" in combined
    assert "explicit type parameters" in combined
    # ...and the old six-space hand-indented fake note is gone.
    assert "      Generic struct constructors" not in combined


@pytest.mark.skipif(not _HAS_SUSHIC, reason="sushic not on PATH")
def test_cross_platform_library_renders_ce3504_through_the_reporter(tmp_path):
    """A LibraryError now renders with its code + caret, not a bare stringified print."""
    from sushi_lang.backend.library_format import LibraryFormat

    libs = tmp_path / "libs"
    libs.mkdir()
    lib_src = tmp_path / "mathlib.sushi"
    lib_src.write_text(
        "public fn add_one(i32 x) i32:\n    return Result.Ok(x + 1)\n",
        encoding="utf-8",
    )
    slib = libs / "mathlib.slib"
    build = subprocess.run(
        ["sushic", "--lib", str(lib_src), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr

    # Mislabel the platform so the load-time check (CE3504) fires on this host.
    metadata, bitcode = LibraryFormat.read(slib)
    metadata["platform"] = "linux" if metadata["platform"] == "darwin" else "darwin"
    LibraryFormat.write(slib, metadata, bitcode)

    project = tmp_path / "consumer"
    project.mkdir()
    (project / "main.sushi").write_text(
        "use <lib/mathlib>\n\n"
        "fn main() i32:\n"
        "    println(add_one(41).realise(-1))\n"
        "    return Result.Ok(0)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs)}
    result = subprocess.run(
        ["sushic", "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "error [CE3504]" in combined
    assert "platform mismatch" in combined
