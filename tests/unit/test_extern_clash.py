"""An `unsafe external` may name a FOREIGN symbol, never one this build defines (#470).

The library's module is merged into the consumer's, and a program's own units share one
module, so a `declare` and a `define` of one name unify. The declaration then calls the
program's own body with no ABI check -- and where the compiler already held a declaration
of the name, the same program was an internal error instead of a diagnostic.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sushic_path import SUSHIC, needs_sushic


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str, kind: str = "binary"):
    libs = tmp_path / "libs"
    libs.mkdir(exist_ok=True)
    _write(tmp_path / f"{name}.sushi", source)
    r = subprocess.run([SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "0.1.0",
                        str(tmp_path / f"{name}.sushi"), "-o", str(libs / f"{name}.slib")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs)}


def _compile(tmp_path: Path, files: dict[str, str], env: dict | None = None,
             name: str = "prog", entry: str = "main.sushi"):
    project = tmp_path / name
    for path, text in files.items():
        _write(project / path, text)
    r = subprocess.run([SUSHIC, entry, "-o", "out"], cwd=project,
                       capture_output=True, text=True, env=env or {**os.environ})
    return r, r.stdout + r.stderr


# A library whose public generic drags a private into the export closure, so the
# consumer registers it -- the other half of what a library can define.
CLOSURE_LIB = """\
fn helper(i32 n) i32:
    return Result.Ok(n * 3)

public fn through@(T)(nom T a, i32 n) i32:
    return Result.Ok(helper(n)??)
"""

KEPT_LIB = """\
public fn double_it(i32 n) i32:
    return Result.Ok(scale(n, 2)??)

fn scale(i32 n, i32 by) i32:
    return Result.Ok(n * by)
"""


# --- What must be refused ----------------------------------------------------------

@needs_sushic
def test_naming_a_public_function_of_another_unit(tmp_path):
    r, out = _compile(tmp_path, {
        "helper.sushi": "public fn shown(i32 n) i32:\n    return Result.Ok(n)\n",
        "main.sushi": """\
use "helper"

unsafe external "C" as raw because "naming a public of another unit":
    fn reach(i32 n) i32 = "shown"

fn main() i32:
    println("{raw.reach(1)}")
    return Result.Ok(0)
""",
    })
    assert r.returncode == 2, out
    assert "CE5013" in out
    assert "CE0000" not in out


@needs_sushic
def test_naming_a_shipped_closure_private_of_a_library(tmp_path):
    env = _build_lib(tmp_path, CLOSURE_LIB, "closlib")
    r, out = _compile(tmp_path, {"main.sushi": """\
use <lib/closlib>

unsafe external "C" as raw because "naming a shipped closure private":
    fn reach(i32 n) i32 = "helper"

fn main() i32:
    println("{through(nom 1, 3).realise(0)}")
    println("{raw.reach(7)}")
    return Result.Ok(0)
"""}, env)
    assert r.returncode == 2, out
    assert "CE5013" in out
    assert "CE0000" not in out


@needs_sushic
def test_naming_a_private_the_library_kept(tmp_path):
    # The silent one: nothing in the consumer's tables held the name before #469, so
    # the declaration bound to the library's body and ran it.
    env = _build_lib(tmp_path, KEPT_LIB, "keptlib")
    r, out = _compile(tmp_path, {"main.sushi": """\
use <lib/keptlib>

unsafe external "C" as raw because "naming a private the library kept":
    fn reach(i32 n, i32 by) i32 = "scale"

fn main() i32:
    println("{double_it(21).realise(0)}")
    println("{raw.reach(4, 2)}")
    return Result.Ok(0)
"""}, env)
    assert r.returncode == 2, out
    assert "CE5013" in out
    assert "keptlib" in out


@needs_sushic
def test_the_diagnostic_says_where_the_symbol_is_defined(tmp_path):
    r, out = _compile(tmp_path, {
        "helper.sushi": "public fn shown(i32 n) i32:\n    return Result.Ok(n)\n",
        "main.sushi": """\
use "helper"

unsafe external "C" as raw because "naming a public of another unit":
    fn reach(i32 n) i32 = "shown"

fn main() i32:
    println("{raw.reach(1)}")
    return Result.Ok(0)
""",
    })
    assert r.returncode == 2, out
    # A relational diagnostic: the note carries the definition's OWN file and line.
    assert "note" in out
    assert "helper.sushi:1:" in out


# --- What must stay legal ----------------------------------------------------------

@needs_sushic
def test_a_foreign_symbol_still_binds_and_runs(tmp_path):
    r, out = _compile(tmp_path, {"main.sushi": """\
unsafe external "C" as libc because "the ordinary case":
    fn strlen(string s) i64 = "strlen"

fn main() i32:
    let string s = "abcd"
    println("{libc.strlen(s)}")
    return Result.Ok(0)
"""})
    assert r.returncode == 0, out

    run = subprocess.run([str(tmp_path / "prog" / "out")], capture_output=True, text=True)
    assert run.stdout.strip() == "4"


@needs_sushic
def test_two_namespaces_may_declare_one_foreign_symbol(tmp_path):
    # `_declare_one` reuses an existing DECLARATION, which is what makes this work. The
    # rule is about a DEFINITION, so it must not touch this.
    r, out = _compile(tmp_path, {
        "helper.sushi": """\
unsafe external "C" as clib because "the same libc symbol, from a second unit":
    fn strlen(string s) i64 = "strlen"

public fn measure(string s) i64:
    return Result.Ok(clib.strlen(s))
""",
        "main.sushi": """\
use "helper"

unsafe external "C" as libc because "the same libc symbol, from the entry unit":
    fn strlen(string s) i64 = "strlen"

fn main() i32:
    let string a = "abcd"
    let string b = "abcdef"
    println("{libc.strlen(a)}")
    println("{measure(b).realise(0)}")
    return Result.Ok(0)
""",
    })
    assert r.returncode == 0, out

    run = subprocess.run([str(tmp_path / "prog" / "out")], capture_output=True, text=True)
    assert run.stdout.split() == ["4", "6"]


@needs_sushic
def test_a_library_public_function_is_callable_as_itself(tmp_path):
    # The rule refuses the FFI route, not the symbol: the ordinary call still works.
    env = _build_lib(tmp_path, KEPT_LIB, "keptlib")
    r, out = _compile(tmp_path, {"main.sushi": """\
use <lib/keptlib>

fn main() i32:
    println("{double_it(21).realise(0)}")
    return Result.Ok(0)
"""}, env)
    assert r.returncode == 0, out

    run = subprocess.run([str(tmp_path / "prog" / "out")], capture_output=True, text=True)
    assert run.stdout.strip() == "42"
