"""Export-closure shipping across .slib boundaries (P2-5 Phase 2 / C4b+C5)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sushic_path import SUSHIC, needs_sushic


CLOSURE_LIB = """\
const i32 SCALE = 3

fn scale_up(i32 x) i32:
    return Result.Ok(x * SCALE)

fn pick_first@(T)(T a, T b) T:
    let T chosen = b
    chosen := a
    return Result.Ok(chosen)

public fn compute@(T)(T a, T b) T:
    let i32 boost = scale_up(2)??
    let T chosen = pick_first(a, b)??
    if (boost == 6):
        return Result.Ok(chosen)
    return Result.Ok(b)
"""

LAMBDA_LIB = """\
fn bump(i32 x) i32:
    return Result.Ok(x + 1)

public fn through@(T)(T a, fn(T) -> i32 f) i32:
    let fn(i32) -> i32 g = |i32 n| bump(n)??
    return Result.Ok(g(1)?? + f(a)??)
"""

EXTERN_LIB = """\
unsafe external "C" as libc because "test fixture":
    fn strlen(string s) i64 = "strlen"

public fn measure@(T)(T a, string s) T:
    let i64 n = libc.strlen(s)
    if (n > 0):
        return Result.Ok(a)
    return Result.Ok(a)
"""

UTIL_SOURCE = """\
use <lib/closurelib>

public fn via_util(i32 a, i32 b) i32:
    let i32 m = compute(a, b).realise(0)
    return Result.Ok(m)
"""

MAIN_SOURCE = """\
use "helpers/util"
use <lib/closurelib>

fn main() i32:
    let i32 a = via_util(10, 20).realise(99)
    let i32 b = compute(7, 8).realise(99)
    println(a)
    println(b)
    return Result.Ok(0)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str = "closurelib"):
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", "binary", "--lib-version", "0.0.0", str(lib_src), "-o", str(libs_dir / f"{name}.slib")],
        cwd=tmp_path, capture_output=True, text=True,
    )
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}
    return result, env


# Producer: CE5006 retained for genuinely un-shippable references

def test_ptr_exposing_private_helper_still_rejected(tmp_path):
    """A private helper whose signature exposes a foreign ptr cannot ship."""
    from sushi_lang.internals.parser import parse_to_ast
    from sushi_lang.internals.report import Reporter
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.passes.collect import StructTable, EnumTable
    from sushi_lang.backend.library_manifest import LibraryManifestGenerator
    from types import SimpleNamespace

    src = (
        "fn touch(ptr p) i32:\n"
        "    return Result.Ok(1)\n"
        "\n"
        "public fn leaky@(T)(T a) T:\n"
        "    let i32 x = touch(a)??\n"
        "    return Result.Ok(a)\n"
    )
    file_path = tmp_path / "ptrlib.sushi"
    file_path.write_text(src, encoding="utf-8")
    program, _ = parse_to_ast(src)
    unit = Unit(name="ptrlib", file_path=file_path, ast=program,
                dependencies=[], public_symbols={})
    reporter = Reporter(source="", filename="ptrlib")
    analyzer = SimpleNamespace(
        reporter=reporter, structs=StructTable(), enums=EnumTable())
    gen = LibraryManifestGenerator(analyzer)

    # Emits and returns; the pipeline's has_errors gate is what stops the build (#436).
    gen._extract_templates([unit])

    assert any(item.code == "CE5006" for item in reporter.items)


@needs_sushic
def test_external_namespace_reference_still_rejected(tmp_path):
    """A generic body calling an unsafe-external namespace cannot ship."""
    result, _ = _build_lib(tmp_path, EXTERN_LIB, name="externlib")
    assert result.returncode != 0
    assert "CE5006" in (result.stdout + result.stderr)


# Consumer: incremental (two-unit) path over the closure

def _build_consumer(tmp_path: Path, env: dict[str, str]) -> Path:
    project = tmp_path / "consumer"
    _write(project / "helpers" / "util.sushi", UTIL_SOURCE)
    _write(project / "main.sushi", MAIN_SOURCE)
    result = subprocess.run(
        [SUSHIC, "main.sushi", "-o", "out"],
        cwd=project, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"Consumer build failed:\n{result.stderr}"
    assert "[rebuilt]" in result.stdout, (
        f"Expected the incremental per-unit path; got:\n{result.stdout}"
    )
    return project


@needs_sushic
def test_closure_links_and_runs_multi_unit(tmp_path):
    """Two consumer units instantiate compute@(T); the private helper, private generic, and
    constant all resolve across the incremental link.
    """
    build, env = _build_lib(tmp_path, CLOSURE_LIB)
    assert build.returncode == 0, f"Library build failed:\n{build.stderr}"
    project = _build_consumer(tmp_path, env)

    out = subprocess.run([str(project / "out")], capture_output=True, text=True)
    assert out.returncode == 0, f"Program failed: {out.stderr}"
    assert out.stdout.split() == ["10", "7"], f"Unexpected output: {out.stdout!r}"


@pytest.mark.skipif(shutil.which("nm") is None, reason="nm not available")
@needs_sushic
def test_private_helper_defined_in_lib_object_only(tmp_path):
    """scale_up must be DEFINED in the library-derived .o and at most referenced (undefined) in
    consumer unit .o files - the consumer declares and links, never re-emits.
    """
    build, env = _build_lib(tmp_path, CLOSURE_LIB)
    assert build.returncode == 0, f"Library build failed:\n{build.stderr}"
    project = _build_consumer(tmp_path, env)

    cache = project / "__sushi_cache__"
    # The symbol carries the declaring unit as a `<unit>$` prefix
    # (`docs/design/unit-namespaces.md` section 9), and a Mach-O name carries a leading
    # underscore. What is being counted is unchanged: definitions of this one helper.
    pattern = re.compile(r"^_?(?:[A-Za-z0-9_$]*\$)?scale_up$")

    def _definitions(obj: Path) -> int:
        nm_out = subprocess.run(["nm", str(obj)], capture_output=True, text=True)
        assert nm_out.returncode == 0, f"nm failed on {obj}:\n{nm_out.stderr}"
        count = 0
        for line in nm_out.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            type_letter, name = parts[-2], parts[-1]
            if pattern.match(name) and type_letter.upper() != "U":
                count += 1
        return count

    lib_objs = sorted(cache.rglob("lib*.o"))
    unit_objs = sorted((cache / "units").rglob("*.o"))
    assert lib_objs and unit_objs

    assert sum(_definitions(o) for o in lib_objs) == 1, (
        "scale_up must be defined exactly once, in the library-derived .o"
    )
    for obj in unit_objs:
        assert _definitions(obj) == 0, (
            f"scale_up must not be re-emitted in consumer unit {obj.name}"
        )


# Consumer: the closure is for the library's own bodies, not for consumer code (#468)

def _consume(tmp_path: Path, env: dict, program: str, name: str = "prog") -> subprocess.CompletedProcess:
    project = tmp_path / name
    _write(project / "main.sushi", program)
    return subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                          cwd=project, capture_output=True, text=True, env=env)


@needs_sushic
def test_a_shipped_private_generic_is_not_callable_by_the_consumer(tmp_path):
    build, env = _build_lib(tmp_path, CLOSURE_LIB)
    assert build.returncode == 0, build.stderr

    r = _consume(tmp_path, env, """\
use <lib/closurelib>

fn main() i32:
    println("{pick_first(1, 2).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out
    assert "pick_first" in out


@needs_sushic
def test_a_shipped_private_concrete_helper_is_not_callable_by_the_consumer(tmp_path):
    build, env = _build_lib(tmp_path, CLOSURE_LIB)
    assert build.returncode == 0, build.stderr

    r = _consume(tmp_path, env, """\
use <lib/closurelib>

fn main() i32:
    println("{scale_up(2).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out
    assert "scale_up" in out


@needs_sushic
def test_a_consumer_generic_reaches_no_further_than_consumer_code(tmp_path):
    # The gate reads the call site, so it has to tell a transplanted library body from a
    # body the user wrote -- including one the user wrote as a generic, whose instance is
    # synthesized exactly like the library's.
    build, env = _build_lib(tmp_path, CLOSURE_LIB)
    assert build.returncode == 0, build.stderr

    r = _consume(tmp_path, env, """\
use <lib/closurelib>

fn wrap@(T)(T a, T b) T:
    return Result.Ok(pick_first(a, b)??)

fn main() i32:
    println("{wrap(1, 2).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out


@needs_sushic
def test_a_lambda_in_a_transplanted_body_still_calls_the_library_helper(tmp_path):
    # The lambda lifts out of the monomorphized template body into a function of its own,
    # which is validated on its own. It is still the library's code and still calls the
    # library's private helper.
    build, env = _build_lib(tmp_path, LAMBDA_LIB, name="lamlib")
    assert build.returncode == 0, build.stderr

    r = _consume(tmp_path, env, """\
use <lib/lamlib>

fn plus_ten(i32 n) i32:
    return Result.Ok(n + 10)

fn main() i32:
    println("{through(2, plus_ten).realise(0)}")
    return Result.Ok(0)
""", name="lamprog")
    assert r.returncode == 0, r.stdout + r.stderr

    out = subprocess.run([str(tmp_path / "lamprog" / "out")], capture_output=True, text=True)
    assert out.stdout.strip() == "14", out.stdout
