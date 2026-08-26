"""What a library declares and does not export (#469).

The export closure ships the privates a public GENERIC's body needs. A private no
template names ships nowhere, so before this key the consumer's answer was CE2008 --
"undefined function" for a function the library defines and deliberately keeps. The
manifest now carries those names, and the CE3005 gate answers for them, so the two
library kinds agree about the wording as well as the legality.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat


KEPT_LIB = """\
public fn double_it(i32 n) i32:
    return Result.Ok(scale(n, 2)??)

fn scale(i32 n, i32 by) i32:
    return Result.Ok(n * by)

fn convert@(T)(nom T x) T:
    return Result.Ok(x)
"""

# A public generic whose body names a private: the closure ships `helper`, so it is
# booked there and not in `not_exported`.
CLOSURE_LIB = """\
fn helper(i32 x) i32:
    return Result.Ok(x + 1)

fn kept(i32 x) i32:
    return Result.Ok(x + 2)

public fn through@(T)(nom T a, i32 n) i32:
    return Result.Ok(helper(n)??)
"""

# The bundled module arrives as an ordinary unit at build time, and its nine private
# helpers are not this library's to declare.
BUNDLED_LIB = """\
use <encoding/msgpack>

fn kept(i32 x) i32:
    return Result.Ok(x)

public fn decode_len(u8[] buf) i32:
    return Result.Ok(buf.len())
"""

LAMBDA_LIB = """\
fn kept(i32 x) i32:
    let fn(i32) -> i32 f = |i32 n| n + 1
    return Result.Ok(f(x)??)

public fn plain(i32 x) i32:
    return Result.Ok(x)
"""

ALL_PUBLIC_LIB = """\
public fn only_one(i32 n) i32:
    return Result.Ok(n)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str = "keptlib",
               kind: str = "binary"):
    """Build `source` as a .slib under tmp_path/libs. Returns (result, env, path)."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    slib = libs_dir / f"{name}.slib"
    result = subprocess.run(
        ["sushic", "--lib", "--lib-kind", kind, "--lib-version", "1.2.0",
         str(lib_src), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}
    return result, env, slib


def _consume(tmp_path: Path, env: dict, program: str, name: str = "prog"):
    project = tmp_path / name
    _write(project / "main.sushi", program)
    return subprocess.run(["sushic", "main.sushi", "-o", "out"],
                          cwd=project, capture_output=True, text=True, env=env)


def _kept(slib: Path) -> dict[str, str]:
    """The `not_exported` key as a name -> kind map."""
    metadata = LibraryFormat.read_metadata_only(slib)
    return {r["name"]: r["kind"] for r in metadata.get("not_exported", [])}


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")


# --- The manifest key --------------------------------------------------------------

@needs_sushic
def test_the_manifest_names_a_kept_function_and_its_kind(tmp_path):
    build, _env, slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    assert _kept(slib) == {"scale": "function", "convert": "generic_function"}


@needs_sushic
def test_a_shipped_closure_private_is_booked_in_the_closure_and_not_here(tmp_path):
    # Each private is named in exactly one place: `helper` carries a signature record in
    # templates.private_functions and already answers CE3005 (#468).
    build, _env, slib = _build_lib(tmp_path, CLOSURE_LIB, name="closlib")
    assert build.returncode == 0, build.stdout + build.stderr

    metadata = LibraryFormat.read_metadata_only(slib)
    shipped = metadata["templates"]["closure_summary"]["private_functions"]
    assert "helper" in shipped
    assert _kept(slib) == {"kept": "function"}


@needs_sushic
def test_a_bundled_module_contributes_no_kept_name(tmp_path):
    # `own_units` is the same filter the `units` index and the source section use. The
    # nine mp_* helpers belong to the bundled module, not to this library.
    build, _env, slib = _build_lib(tmp_path, BUNDLED_LIB, name="bundlib")
    assert build.returncode == 0, build.stdout + build.stderr

    kept = _kept(slib)
    assert kept == {"kept": "function"}
    assert not [n for n in kept if n.startswith("mp_")]


@needs_sushic
def test_a_lifted_lambda_contributes_no_kept_name(tmp_path):
    # A lifted lambda is in unit.ast.functions by manifest time and is not public, but
    # it is no name a consumer can write.
    build, _env, slib = _build_lib(tmp_path, LAMBDA_LIB, name="lamlib")
    assert build.returncode == 0, build.stdout + build.stderr

    assert _kept(slib) == {"kept": "function"}


@needs_sushic
def test_a_library_with_no_privates_carries_no_key(tmp_path):
    build, _env, slib = _build_lib(tmp_path, ALL_PUBLIC_LIB, name="publib")
    assert build.returncode == 0, build.stdout + build.stderr

    assert "not_exported" not in LibraryFormat.read_metadata_only(slib)


# --- The diagnostic ----------------------------------------------------------------

@needs_sushic
def test_a_kept_concrete_function_is_private_and_not_undefined(tmp_path):
    build, env, _slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn main() i32:
    println("{scale(4, 2).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out
    assert "CE2008" not in out
    # The consumer must be told WHERE the function lives, or "private" is no better than
    # "undefined": the library is the one place they can go and read it.
    assert "keptlib" in out


@needs_sushic
def test_a_kept_generic_is_private_and_draws_no_mode_advice(tmp_path):
    # An unresolved callee once drew CE2427 after the CE2008, advising the user to drop a
    # `nom` the library declares. The callee is still unresolved on this path.
    build, env, _slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn main() i32:
    let i32 n = 7
    println("{convert(nom n).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out
    assert "CE2427" not in out


@needs_sushic
def test_a_name_no_unit_and_no_library_declares_is_still_undefined(tmp_path):
    build, env, _slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn main() i32:
    println("{no_such_thing(1).realise(0)}")
    return Result.Ok(0)
""")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE2008" in out
    assert "CE3005" not in out


@needs_sushic
def test_the_consumer_may_define_a_name_the_library_kept(tmp_path):
    # A kept private ships nowhere, so there is nothing to clash with: no CE5007, and the
    # consumer's own function is what the call resolves to.
    build, env, _slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn scale(i32 n, i32 by) i32:
    return Result.Ok(n * by * 10)

fn main() i32:
    println("{scale(4, 2).realise(0)}")
    return Result.Ok(0)
""")
    assert r.returncode == 0, r.stdout + r.stderr

    run = subprocess.run([str(tmp_path / "prog" / "out")],
                         capture_output=True, text=True)
    assert run.stdout.strip() == "80"


@needs_sushic
def test_both_library_kinds_answer_alike(tmp_path):
    build, env, _slib = _build_lib(tmp_path, KEPT_LIB, kind="source")
    assert build.returncode == 0, build.stdout + build.stderr

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn main() i32:
    println("{scale(4, 2).realise(0)}")
    return Result.Ok(0)
""", name="srcprog")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE3005" in out


# --- Forward compatibility ----------------------------------------------------------

@needs_sushic
def test_a_library_without_the_key_still_rejects_the_call(tmp_path):
    # An older .slib read by a newer compiler has no key, and answers as it always did.
    build, env, slib = _build_lib(tmp_path, KEPT_LIB)
    assert build.returncode == 0, build.stdout + build.stderr

    metadata, bitcode = LibraryFormat.read(slib)
    metadata.pop("not_exported")
    LibraryFormat.write(slib, metadata, bitcode)

    r = _consume(tmp_path, env, """\
use <lib/keptlib>

fn main() i32:
    println("{scale(4, 2).realise(0)}")
    return Result.Ok(0)
""", name="oldprog")
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "CE2008" in out


# --- The registry, without a build --------------------------------------------------

def test_the_registry_reads_the_key():
    from sushi_lang.semantics.library_registry import LibraryRegistry

    registry = LibraryRegistry()
    registry.register_library(
        lib_path=Path("keptlib.slib"),
        manifest={
            "library_name": "keptlib",
            "public_functions": [],
            "not_exported": [
                {"name": "scale", "kind": "function"},
                {"name": "convert", "kind": "generic_function"},
            ],
        },
    )

    assert registry.get_all_not_exported() == {
        "scale": ("keptlib", "function"),
        "convert": ("keptlib", "generic_function"),
    }


def test_a_manifest_without_the_key_reads_as_empty():
    from sushi_lang.semantics.library_registry import LibraryRegistry

    registry = LibraryRegistry()
    registry.register_library(lib_path=Path("plain.slib"),
                              manifest={"library_name": "plain",
                                        "public_functions": []})

    assert registry.get_all_not_exported() == {}
