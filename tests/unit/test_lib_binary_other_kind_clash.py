"""A consumer type against a BINARY library's public type of the OTHER kind (#902).

A type name is one per program, and a struct and an enum share it. A consumer
declaration of a name a binary library exports publicly is CE3011 at the declaration,
whatever the two kinds are, and nothing else is said. The same-kind half is
`test_lib_binary_type_clash.py`.

`EXPECT_ERROR_CODE` is a substring test and cannot prove a code is ABSENT, so the code
LIST is asserted here, on the whole rendered output.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic

_TESTS = Path(__file__).resolve().parents[1]
_FIXTURES = _TESTS / "libs" / "binary_type_clash_other_kind"

GENERIC_ENUM_AGAINST_STRUCT = """\
use <lib/binary_api_lib>

enum Crate@(T):
    Empty
    Full(T)

fn main() i32:
    let Crate@(i32) c = Crate.Full(3)
    match c:
        Crate.Empty -> println("e")
        Crate.Full(v) -> println("{v}")
    return Result.Ok(0)
"""

GENERIC_STRUCT_AGAINST_ENUM = """\
use <lib/binary_api_lib>

struct Mood@(T):
    T m

fn main() i32:
    let Mood@(i32) c = Mood(3)
    println("{c.m}")
    return Result.Ok(0)
"""

_CASES = {
    "enum_against_struct": (_FIXTURES / "test_err_lib_binary_enum_against_struct.sushi")
    .read_text(encoding="utf-8"),
    "struct_against_enum": (_FIXTURES / "test_err_lib_binary_struct_against_enum.sushi")
    .read_text(encoding="utf-8"),
    "generic_enum_against_struct": GENERIC_ENUM_AGAINST_STRUCT,
    "generic_struct_against_enum": GENERIC_STRUCT_AGAINST_ENUM,
}


@pytest.fixture(scope="module")
def lib_env(tmp_path_factory) -> dict:
    """Build the binary helper library once and return the consumer's environment."""
    libs_dir = tmp_path_factory.mktemp("libs")
    built = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", "binary", "--lib-version", "1.0.0",
         str(_TESTS / "libs" / "helpers" / "binary_api_lib.sushi"),
         "-o", str(libs_dir / "binary_api_lib.slib"), "--cache-dir",
         str(libs_dir / "cache")],
        capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"}


@needs_sushic
@pytest.mark.parametrize("case", list(_CASES))
def test_other_kind_is_one_ce3011(tmp_path, lib_env, case):
    """One CE3011 at the consumer's declaration, naming the library, and no cascade."""
    (tmp_path / "main.sushi").write_text(_CASES[case], encoding="utf-8")
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out", "--cache-dir", str(tmp_path / "cache")],
        cwd=tmp_path, capture_output=True, text=True, env=lib_env)
    out = result.stdout + result.stderr

    assert result.returncode == 2, out
    assert re.findall(r"\b(C[EW]\d{4})\b", out) == ["CE3011"], out
    assert "binary_api_lib" in out, out
