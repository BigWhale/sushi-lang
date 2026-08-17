"""`copy()` gives the destination the mode it asks for (issue #363).

`declare_open` declared the variadic `open(2)` with a FIXED third parameter. Apple arm64
passes a variadic argument on the stack and a fixed one in a register, so the callee read the
mode from the wrong place. The destination came out with whatever happened to be there --
`0140` in one program, `0540` in another -- which is why the assertion here is the exact mode
and not a read of the file. A read-based assertion passes whenever the garbage happens to
carry the owner read bit, and the sibling `tests/stdlib/io/test_file_copy.sushi` only calls
`exists()`, which needs no permission at all.

Python's `os.stat` rather than a Sushi assertion: the language has no way to read a file mode,
and `stat`'s struct layout is platform-specific, so FFI would be the less portable half.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUSHIC = shutil.which("sushic")

# The mode `copy()` asks for, in `generate_copy`.
EXPECTED_MODE = 0o644

PROGRAM = """use <io/files>

fn main() i32:
    match copy('{src}', '{dst}'):
        Result.Ok(_) -> println("copied")
        Result.Err(_) -> return Result.Ok(1)
    return Result.Ok(0)
"""


@pytest.mark.skipif(SUSHIC is None, reason="sushic not on PATH (run under `uv run pytest`)")
def test_copy_gives_the_destination_the_mode_it_asks_for(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("Mostly Harmless", encoding="utf-8")

    (tmp_path / "main.sushi").write_text(
        PROGRAM.format(src=src, dst=dst), encoding="utf-8")
    compiled = subprocess.run(["sushic", "main.sushi", "-o", "out"],
                              cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    run = subprocess.run([str(tmp_path / "out")], cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0 and "copied" in run.stdout, run.stdout + run.stderr
    assert dst.exists(), "copy() reported success but wrote no destination"

    actual = stat.S_IMODE(os.stat(dst).st_mode)
    # The umask applies to the mode open(2) is given, exactly as it would in C.
    expected = EXPECTED_MODE & ~_umask()
    assert actual == expected, (
        f"copy() gave the destination mode {actual:04o}, not {expected:04o}. The mode "
        "argument is not reaching open(2) -- check that declare_open declares it variadic."
    )


def _umask() -> int:
    """The process umask, read without leaving it changed."""
    current = os.umask(0o022)
    os.umask(current)
    return current
