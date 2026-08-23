"""A consumer compiles a source library as ordinary compilation units.

This is the half that makes source-first real: `use <lib/foo>` on a source `.slib`
injects its units into the consumer's unit table, and the ordinary passes take it from
there. See `docs/design/libraries.md` section 4.2.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from sushi_lang.backend.library_format import LibraryFormat


LIB_MAIN = """\
use "mathhelper"

public fn twice(i32 n) i32:
    return Result.Ok(scale(n, 2)??)

public fn describe(i32 n) string:
    return Result.Ok("n is {n}")
"""

LIB_HELPER = """\
fn clamp(i32 n) i32:
    return Result.Ok(n)

public fn scale(i32 n, i32 by) i32:
    return Result.Ok(clamp(n)?? * by)
"""

GENERIC_LIB = """\
public fn pick@(T)(nom T a) T:
    return Result.Ok(a)
"""

ITER_LIB = """\
use <collections/iter>

public fn just_one() i32:
    return Result.Ok(1)
"""


def _sushic(args, cwd, extra_env=None):
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["sushic", *args], cwd=cwd, capture_output=True,
                          text=True, env=env)


def _build_lib(tmp_path, sources, main, kind="source", version="1.2.0"):
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    for name, text in sources.items():
        path = tmp_path / f"{name}.sushi"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    out = tmp_path / f"{main}.slib"
    r = _sushic(["--lib", "--lib-kind", kind, "--lib-version", version,
                 str(tmp_path / f"{main}.sushi"), "-o", str(out)], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def _run_consumer(tmp_path, program, lib_dir, name="consumer"):
    src = tmp_path / f"{name}.sushi"
    src.write_text(program, encoding="utf-8")
    build = _sushic([str(src)], cwd=tmp_path, extra_env={"SUSHI_LIB_PATH": str(lib_dir)})
    return build, tmp_path / name


# --- The library actually runs ----------------------------------------------

@pytest.fixture(scope="module")
def mathlib(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mathlib")
    _build_lib(tmp, {"mathlib": LIB_MAIN, "mathhelper": LIB_HELPER}, "mathlib")
    return tmp


def test_a_consumer_compiles_and_runs_against_a_source_library(tmp_path, mathlib):
    build, exe = _run_consumer(tmp_path, """\
use <lib/mathlib>

fn main() i32:
    println("{twice(21).realise(0)}")
    return Result.Ok(0)
""", mathlib)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "42\n"


def test_a_public_symbol_from_a_deeper_library_unit_is_callable(tmp_path, mathlib):
    build, exe = _run_consumer(tmp_path, """\
use <lib/mathlib>

fn main() i32:
    println("{scale(7, 6).realise(0)}")
    return Result.Ok(0)
""", mathlib)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "42\n"


def test_a_private_library_function_stays_private(tmp_path, mathlib):
    # No export closure and no CE5007: unit privacy already carries this.
    build, _exe = _run_consumer(tmp_path, """\
use <lib/mathlib>

fn main() i32:
    println("{clamp(1).realise(0)}")
    return Result.Ok(0)
""", mathlib)
    assert build.returncode != 0
    assert "clamp" in build.stdout + build.stderr


# --- Namespacing -------------------------------------------------------------

def test_a_library_unit_may_share_a_name_with_a_consumer_unit(tmp_path, mathlib):
    (tmp_path / "mathhelper.sushi").write_text("""\
public fn local_only() i32:
    return Result.Ok(99)
""", encoding="utf-8")
    build, exe = _run_consumer(tmp_path, """\
use <lib/mathlib>
use "mathhelper"

fn main() i32:
    println("{local_only().realise(0)} {twice(1).realise(0)}")
    return Result.Ok(0)
""", mathlib)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "99 2\n"


# --- What the library itself depends on --------------------------------------

def test_a_library_that_uses_a_bundled_stdlib_module_compiles(tmp_path_factory, tmp_path):
    lib_dir = tmp_path_factory.mktemp("iterlib")
    _build_lib(lib_dir, {"iterlib": ITER_LIB}, "iterlib")

    build, exe = _run_consumer(tmp_path, """\
use <lib/iterlib>

fn main() i32:
    println("{just_one().realise(0)}")
    return Result.Ok(0)
""", lib_dir)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "1\n"


def test_a_library_generic_monomorphizes_at_the_consumer(tmp_path_factory, tmp_path):
    lib_dir = tmp_path_factory.mktemp("genlib")
    _build_lib(lib_dir, {"genlib": GENERIC_LIB}, "genlib")

    build, exe = _run_consumer(tmp_path, """\
use <lib/genlib>

fn main() i32:
    let i32 n = 41
    println("{pick(nom n).realise(0) + 1}")
    return Result.Ok(0)
""", lib_dir)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "42\n"


# --- The A3 payoff, end to end ------------------------------------------------

def test_a_source_library_built_elsewhere_still_compiles(tmp_path_factory, tmp_path):
    lib_dir = tmp_path_factory.mktemp("foreignlib")
    slib = _build_lib(lib_dir, {"mathlib": LIB_MAIN, "mathhelper": LIB_HELPER}, "mathlib")

    metadata, source = LibraryFormat.read_source_only(slib)
    metadata["platform"] = "some-other-platform"
    LibraryFormat.write(slib, metadata, b"", source=source)

    build, exe = _run_consumer(tmp_path, """\
use <lib/mathlib>

fn main() i32:
    println("{twice(21).realise(0)}")
    return Result.Ok(0)
""", lib_dir)
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout == "42\n"


# --- Diagnostics for code the consumer did not write --------------------------

def test_an_error_inside_library_source_names_the_library(tmp_path_factory, tmp_path):
    lib_dir = tmp_path_factory.mktemp("brokenlib")
    slib = _build_lib(lib_dir, {"brokenlib": LIB_MAIN, "mathhelper": LIB_HELPER}, "brokenlib")

    metadata, source = LibraryFormat.read_source_only(slib)
    source["mathhelper"] = """\
public fn scale(i32 n, i32 by) i32:
    return Result.Ok(no_such_symbol(n))
"""
    LibraryFormat.write(slib, metadata, b"", source=source)

    build, _exe = _run_consumer(tmp_path, """\
use <lib/brokenlib>

fn main() i32:
    println("{twice(21).realise(0)}")
    return Result.Ok(0)
""", lib_dir)
    assert build.returncode != 0
    out = build.stdout + build.stderr
    # The consumer must never see a bare error about code they did not write: the
    # library and its version have to appear alongside the failure.
    assert "brokenlib" in out
    assert "1.2.0" in out


# --- Caching ------------------------------------------------------------------

def test_a_second_build_reuses_the_cached_library_units(tmp_path, mathlib):
    program = """\
use <lib/mathlib>

fn main() i32:
    println("{twice(21).realise(0)}")
    return Result.Ok(0)
"""
    first, _exe = _run_consumer(tmp_path, program, mathlib)
    assert first.returncode == 0, first.stdout + first.stderr

    second, _exe = _run_consumer(tmp_path, program, mathlib)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "[cached]" in second.stdout


# --- The default ---------------------------------------------------------------

def test_source_is_now_the_default_kind(tmp_path):
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    (tmp_path / "plain.sushi").write_text(GENERIC_LIB, encoding="utf-8")
    out = tmp_path / "plain.slib"
    r = _sushic(["--lib", "--lib-version", "1.0.0",
                 str(tmp_path / "plain.sushi"), "-o", str(out)], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    metadata, source = LibraryFormat.read_source_only(out)
    assert metadata["kind"] == "source"
    assert "plain" in source


def test_two_instantiations_of_one_library_generic_share_a_cache(tmp_path_factory,
                                                                 tmp_path):
    # A monomorphized instance is stored in the unit that DECLARED the generic, so a
    # library unit's object depends on what the consumer asked for. Its fingerprint has
    # to say so, or the second program reuses the first one's object and the linker
    # cannot find its instance.
    lib_dir = tmp_path_factory.mktemp("sharedgen")
    _build_lib(lib_dir, {"sharedgen": GENERIC_LIB}, "sharedgen")

    first, exe_a = _run_consumer(tmp_path, """\
use <lib/sharedgen>

fn main() i32:
    let i32 n = 41
    println("{pick(nom n).realise(0) + 1}")
    return Result.Ok(0)
""", lib_dir, name="prog_a")
    assert first.returncode == 0, first.stdout + first.stderr

    second, exe_b = _run_consumer(tmp_path, """\
use <lib/sharedgen>

fn main() i32:
    let string s = "hello"
    println(pick(nom s).realise("no"))
    return Result.Ok(0)
""", lib_dir, name="prog_b")
    assert second.returncode == 0, second.stdout + second.stderr

    assert subprocess.run([str(exe_a)], capture_output=True, text=True).stdout == "42\n"
    assert subprocess.run([str(exe_b)], capture_output=True, text=True).stdout == "hello\n"
