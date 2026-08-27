"""A consumer may shadow a library's export, and its own declaration answers its call.

Decision 10 of `docs/design/visibility.md`. A private function is emitted with internal
linkage, so the consumer's declaration and the library's are two symbols and neither
call site changes meaning: the consumer's call binds to the consumer's function, and the
library's body keeps calling its own. The only combination that could break the link --
both public -- is CE3003 already.

Two things follow, and both are asserted here. The frontend has to agree with the linker
about which declaration answers the consumer's call, which it did not: the merge kept the
library's signature, so a consumer replacement with a DIFFERENT signature was refused with
CE2009. And shadowing an export is legal but rarely intended, so it warns with CW3002.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")

LIBRARY = """\
public fn get_value() i32:
    return Result.Ok(100)

public fn use_value() i32:
    return Result.Ok(get_value()?? * 2)

public fn library_only() i32:
    return Result.Ok(42)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_source_lib(tmp_path: Path, source: str, name: str) -> dict:
    """Build `source` as a SOURCE .slib, and return an env pointing at it."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    result = subprocess.run(
        ["sushic", "--lib", "--lib-kind", "source", "--lib-version", "1.0.0",
         str(lib_src), "-o", str(libs_dir / f"{name}.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"}


def _consume(tmp_path: Path, env: dict, program: str) -> tuple[str, Path]:
    """Compile a consumer program. Returns its diagnostics and the binary path."""
    project = tmp_path / "prog"
    _write(project / "main.sushi", program)
    binary = project / "out"
    result = subprocess.run(["sushic", "main.sushi", "-o", str(binary)],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.stdout + result.stderr, binary


def _run(binary: Path) -> str:
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@needs_sushic
def test_a_shadowing_declaration_with_a_different_signature_is_accepted(tmp_path):
    """The merge used to keep the library's signature, so this was CE2009."""
    env = _build_source_lib(tmp_path, LIBRARY, "siglib")
    out, binary = _consume(tmp_path, env, """\
use <lib/siglib>

fn get_value(i32 n) i32:
    return Result.Ok(n + 7)

fn main() i32:
    println("mine = {get_value(1).realise(0)}")
    return Result.Ok(0)
""")
    assert "CE2009" not in out, out
    assert "error" not in out, out
    assert "mine = 8" in _run(binary)


@needs_sushic
def test_shadowing_a_library_export_warns(tmp_path):
    env = _build_source_lib(tmp_path, LIBRARY, "warnlib")
    out, _binary = _consume(tmp_path, env, """\
use <lib/warnlib>

fn get_value() i32:
    return Result.Ok(500)

fn main() i32:
    println("{get_value().realise(0)}")
    return Result.Ok(0)
""")
    assert "CW3002" in out, out
    assert "get_value" in out, out


@needs_sushic
def test_the_librarys_own_body_still_calls_its_own_function(tmp_path):
    """The runtime half of `tests/libs/test_warn_lib_override.sushi`'s contract.

    A `test_warn_` file's binary is not executed, so the priority contract -- the
    consumer's own call gets 500, the library's body still gets 200 -- is asserted here.
    """
    env = _build_source_lib(tmp_path, LIBRARY, "linklib")
    out, binary = _consume(tmp_path, env, """\
use <lib/linklib>

fn get_value() i32:
    return Result.Ok(500)

fn main() i32:
    println("direct  = {get_value().realise(0)}")
    println("library = {use_value().realise(0)}")
    println("only    = {library_only().realise(0)}")
    return Result.Ok(0)
""")
    assert "error" not in out, out
    printed = _run(binary)
    assert "direct  = 500" in printed, printed
    assert "library = 200" in printed, printed
    assert "only    = 42" in printed, printed


@needs_sushic
def test_a_name_the_consumer_does_not_redeclare_does_not_warn(tmp_path):
    env = _build_source_lib(tmp_path, LIBRARY, "quietlib")
    out, binary = _consume(tmp_path, env, """\
use <lib/quietlib>

fn main() i32:
    println("only = {library_only().realise(0)}")
    return Result.Ok(0)
""")
    assert "CW3002" not in out, out
    assert "only = 42" in _run(binary)
