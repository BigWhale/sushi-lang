"""Differential test: the Sushi io/path module against Python posixpath.

Each case runs through the compiled tests/stdlib/path/helpers/pathcalc.sushi
tool (one op per stdin line) and compares against the posixpath mirror.
"""
from __future__ import annotations

import posixpath
import subprocess
from pathlib import Path

import pytest
from sushic_path import SUSHIC, SUSHIC_AVAILABLE

REPO = Path(__file__).parents[2]
CALC_SRC = REPO / "tests" / "stdlib" / "path" / "helpers" / "pathcalc.sushi"

PATHS = [
    "", "/", "//", "///", ".", "..", "a", "a/", "a/b", "a/b/", "/a", "/a/b",
    "/a/b/", "a//b", "a/./b", "a/../b", "../a", "./a", "/..", "/../a",
    "a/b/..", "a/b/../..", "a/b/../../..", "/a/b/../../..", ".hidden",
    ".hidden.txt", "a.tar.gz", "dir.d/plain", "a/.b", "a..b", "...",
    "a/b.c/d", "//a/b", "trailing/", "/only",
]

JOINS = [
    ("a", "b"), ("a/", "b"), ("a", "/b"), ("", "b"), ("a", ""), ("", ""),
    ("/", "b"), ("a/b", "c/d"), ("a", "b/"), ("/a/", "/b/"),
]


def _extension_mirror(p: str) -> str:
    ext = posixpath.splitext(p)[1]
    return ext[1:] if ext.startswith(".") else ext


def _cases() -> list[tuple[str, str, str, str]]:
    cases = []
    for p in PATHS:
        cases.append(("basename", p, "", posixpath.basename(p)))
        cases.append(("dirname", p, "", posixpath.dirname(p)))
        cases.append(("extension", p, "", _extension_mirror(p)))
        cases.append(("normalize", p, "", posixpath.normpath(p)))
    for a, b in JOINS:
        cases.append(("join", a, b, posixpath.join(a, b)))
    return cases


@pytest.fixture(scope="module")
def calc_tool(tmp_path_factory):
    """Compile the pathcalc helper once for the module."""
    if not SUSHIC_AVAILABLE:
        pytest.skip("no compiler driver in this checkout")
    tmp = tmp_path_factory.mktemp("pathcalc")
    out = tmp / "pathcalc"
    result = subprocess.run(
        [SUSHIC, str(CALC_SRC), "-o", str(out)],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return out


def test_path_matches_posixpath(calc_tool):
    cases = _cases()
    stdin = "".join(f"{op}|{a}|{b}\n" for op, a, b, _ in cases)
    result = subprocess.run(
        [str(calc_tool)], input=stdin, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    got = result.stdout.split("\n")
    for i, (op, a, b, expected) in enumerate(cases):
        label = f"{op}({a!r}, {b!r})" if op == "join" else f"{op}({a!r})"
        assert got[i] == expected, f"{label}: sushi={got[i]!r} posixpath={expected!r}"
