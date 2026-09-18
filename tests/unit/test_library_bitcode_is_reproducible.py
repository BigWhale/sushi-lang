"""Two builds of one library produce one bitcode (#708).

A global named after what it holds took its suffix from `hash()`, which Python
randomizes per process, so the same string literal was `@.str.6_3037160281_raw` in one
build and `@.str.6_2413483906_raw` in the next. Nothing is wrong at run time, and that
is the point: the library's bitcode cannot be compared byte for byte between two builds,
and no content-addressed cache can be laid over it.

`PYTHONHASHSEED` is set to two different values on purpose. It is the one knob that
makes the fault show every time rather than once in a while.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

# An interpolation hole names a string constant, and `debug()` names one of its own.
# Both globals are named after their text, so both carry the suffix under test.
LIBRARY = """\
use <collections/hashmap>

public fn describe(i32 n) string:
    return Result.Ok("count {n} calm")

public fn dump(i32 n) ~:
    let HashMap@(i32, string) seen = HashMap.new()
    seen.insert(n, "one")
    seen.debug()
    seen.free()
    return Result.Ok(~)
"""


def _build(tmp_path: Path, name: str, seed: str) -> bytes:
    """Build the library under one hash seed and answer its bitcode."""
    slib = tmp_path / f"{name}.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", "binary", "--lib-version", "1.0.0",
         str(tmp_path / "reprolib.sushi"), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PYTHONHASHSEED": seed})
    assert result.returncode == 0, result.stdout + result.stderr
    return LibraryFormat.read(slib)[1]


@pytest.mark.skipif(not SUSHIC_AVAILABLE, reason=f"no compiler driver at {SUSHIC}")
def test_two_builds_of_one_library_agree(tmp_path):
    (tmp_path / "reprolib.sushi").write_text(LIBRARY, encoding="utf-8")

    first = _build(tmp_path, "first", "7")
    second = _build(tmp_path, "second", "1009")

    assert first == second
