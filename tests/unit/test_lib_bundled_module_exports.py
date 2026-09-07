"""A bundled module's declarations are not the library's API (#594).

A `use <io/fs>` injects the bundled Sushi-source module as an ordinary compilation
unit, so it reaches the manifest generator beside the library's own files. The `units`
index and the source section filter it; the six extractors did not, so a library that
declared one function shipped eleven, plus the structs and the perks of `<io/fs>`,
`<io/path>` and `<io/contracts>`. A consumer that imported the same module then read a
second definition of each name and answered CE4001.

The same filter answers for an injected source LIBRARY's units, which carry the same
provenance and are equally not this library's to declare (limitation 1 of
`docs/libraries.md`: a consumer states each library it uses).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushic_path import SUSHIC, SUSHIC_AVAILABLE, needs_sushic


# `<io/fs>` carries public functions, public structs and -- through its `public use`
# chain -- the three `<io/contracts>` perks and their `File` implementations.
# `<encoding/msgpack>` carries public enums and public generic functions.
# `<io/buf>` carries public generic structs.
BUNDLED_LIB = """\
use <io/fs>
use <encoding/msgpack>
use <io/buf>

fn helper(i32 n) i32:
    return Result.Ok(n + 1)

fn kept(i32 n) i32:
    return Result.Ok(n + 2)

public fn greet() string:
    return Result.Ok("Mostly Harmless")

public fn through@(T)(nom T a, i32 n) i32:
    return Result.Ok(helper(n)??)
"""

# A .bc module, not a source one: the link fault predates the source-stdlib modules
# entirely, and this is the shape that proves it.
BITCODE_LIB = """\
use <collections/strings>

public fn shout(string s) string:
    return Result.Ok(s.trim())
"""

PLAIN_LIB = """\
public fn a_one() i32:
    return Result.Ok(1)
"""

IMPORTER_LIB = """\
use <lib/liba>

public fn b_two() i32:
    return Result.Ok(a_one()??)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str, kind: str = "binary",
               env: dict | None = None):
    """Build `source` as a .slib under tmp_path/libs. Returns (result, env, path)."""
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    slib = libs_dir / f"{name}.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-kind", kind, "--lib-version", "1.0.0",
         str(lib_src), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True,
        env=env or {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)},
    )
    return result, {**os.environ, "SUSHI_LIB_PATH": str(libs_dir)}, slib


def _consume(tmp_path: Path, env: dict, program: str, name: str = "prog"):
    project = tmp_path / name
    _write(project / "main.sushi", program)
    return subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                          cwd=project, capture_output=True, text=True, env=env)


@pytest.fixture(scope="module")
def bundled(tmp_path_factory):
    """One binary library over three bundled source modules, built once."""
    if not SUSHIC_AVAILABLE:
        pytest.skip(f"no compiler driver at {SUSHIC}")
    tmp_path = tmp_path_factory.mktemp("bundled")
    build, env, slib = _build_lib(tmp_path, BUNDLED_LIB, "mylib")
    assert build.returncode == 0, build.stdout + build.stderr
    return tmp_path, env, LibraryFormat.read_metadata_only(slib)


def _names(records) -> set[str]:
    return {r["name"] for r in records or ()}


# --- The manifest indexes ----------------------------------------------------------

def test_the_units_index_names_only_the_librarys_own_unit(bundled):
    _tmp, _env, metadata = bundled
    assert metadata["units"] == ["mylib"]


def test_a_bundled_module_contributes_no_public_function(bundled):
    _tmp, _env, metadata = bundled
    assert _names(metadata["public_functions"]) == {"greet"}


def test_a_bundled_module_contributes_no_public_struct(bundled):
    # `File` and `FileStat` belong to <io/fs>.
    _tmp, _env, metadata = bundled
    assert _names(metadata["structs"]) == set()


def test_a_bundled_module_contributes_no_public_enum(bundled):
    # `MsgValue` and `MpError` belong to <encoding/msgpack>.
    _tmp, _env, metadata = bundled
    assert _names(metadata["enums"]) == set()


def test_a_bundled_module_contributes_no_perk(bundled):
    # `Reader`, `Writer` and `Seek` belong to <io/contracts>.
    _tmp, _env, metadata = bundled
    assert _names(metadata["templates"]["perks"]) == set()


def test_a_bundled_module_contributes_no_perk_implementation(bundled):
    _tmp, _env, metadata = bundled
    assert metadata["templates"]["perk_impls"] == []
    assert metadata["templates"]["generic_perk_impls"] == []


def test_a_bundled_module_contributes_no_template(bundled):
    # `BufReader@(R)`, `Lines@(R)` and `BufWriter@(W)` belong to <io/buf>.
    _tmp, _env, metadata = bundled
    templates = metadata["templates"]
    assert _names(templates["generic_structs"]) == set()
    assert _names(templates["generic_enums"]) == set()
    assert _names(templates["generic_functions"]) == {"through"}


def test_the_export_closure_ships_the_librarys_own_private_and_no_other(bundled):
    # `through@(T)` names `helper`, so the closure ships it. A bundled module's
    # privates are indexed nowhere, so the flat fallback cannot reach one.
    _tmp, _env, metadata = bundled
    summary = metadata["templates"]["closure_summary"]
    assert summary["private_functions"] == ["helper"]
    assert summary["private_generic_functions"] == []
    assert summary["constants"] == []
    assert summary["private_types"] == []


def test_a_bundled_module_contributes_no_kept_name(bundled):
    # `kept` is the library's own; nothing of <io/fs>, <encoding/msgpack> or <io/buf>
    # joins it.
    _tmp, _env, metadata = bundled
    assert {r["name"]: r["kind"] for r in metadata["not_exported"]} == {
        "kept": "function"}


# --- The consumer ------------------------------------------------------------------

CONSUMER = """\
use <lib/mylib>
use <io/fs>

fn main() i32:
    let string who = greet().realise('nobody')
    println("Hello, {who}!")
    return Result.Ok(0)
"""


def test_a_consumer_may_import_the_same_bundled_module(bundled):
    # The front end read a second definition of each of the module's names and
    # answered CE4001 -- against the STDLIB source, for a program that named none of
    # it. Nothing of the module is in the manifest now, so nothing is registered twice.
    tmp_path, env, _metadata = bundled
    r = _consume(tmp_path, env, CONSUMER)
    out = r.stdout + r.stderr
    assert "CE4001" not in out, out
    assert "src_sushi" not in out, out


def test_a_consumer_may_import_the_same_bundled_module_and_link(tmp_path_factory,
                                                                bundled):
    """The other half: the bitcode carries the module's code, and `ld` saw two.

    A compiled library is self-contained, so the copy stays -- it is weakened instead
    (`backend/library_linkage.py`). The consumer here is MULTI-UNIT by construction:
    the module it imports is injected as a unit, so the build takes the per-unit
    incremental path and hands `ld` both objects. The monolithic path never showed the
    fault, because `TwoPhaseLinker` resolves a duplicate by symbol source.
    """
    tmp_path, env, _metadata = bundled
    r = _consume(tmp_path, env, CONSUMER, name="linkprog")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "duplicate symbol" not in out, out

    run = subprocess.run([str(tmp_path / "linkprog" / "out")],
                         capture_output=True, text=True)
    assert run.stdout == "Hello, Mostly Harmless!\n", run.stdout + run.stderr


def test_a_consumer_that_imports_none_of_it_still_links(bundled):
    # The weakened copy is still a DEFINITION: a library over `<io/fs>` links for a
    # consumer that names nothing of it, which is what `linkonce_odr` would not
    # guarantee once the library itself stopped referencing a symbol.
    tmp_path, env, _metadata = bundled
    r = _consume(tmp_path, env, """\
use <lib/mylib>

fn main() i32:
    let string who = greet().realise('nobody')
    println("Hello, {who}!")
    return Result.Ok(0)
""", name="soloprog")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "undefined" not in out.lower(), out


@needs_sushic
def test_a_bitcode_module_is_no_second_definition_either(tmp_path):
    # 39 duplicate symbols before the weakening, and the consumer has to be MULTI-UNIT
    # for `ld` to be the one linking: a single-unit program takes the monolithic path.
    build, env, _slib = _build_lib(tmp_path, BITCODE_LIB, "strlib")
    assert build.returncode == 0, build.stdout + build.stderr

    project = tmp_path / "strprog"
    _write(project / "helper.sushi", """\
public fn nudge(i32 n) i32:
    return Result.Ok(n + 1)
""")
    _write(project / "main.sushi", """\
use <lib/strlib>
use <collections/strings>
use "helper"

fn main() i32:
    let string a = shout(' hi ').realise('x')
    let string b = ' yo '.trim()
    println("{a}{b}{nudge(1).realise(0)}")
    return Result.Ok(0)
""")
    r = subprocess.run([SUSHIC, "main.sushi", "-o", "out"],
                       cwd=project, capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "duplicate symbol" not in out, out

    run = subprocess.run([str(project / "out")], capture_output=True, text=True)
    assert run.stdout == "hiyo2\n", run.stdout + run.stderr


# --- An injected source library ----------------------------------------------------

@needs_sushic
def test_an_imported_source_library_is_not_re_exported(tmp_path):
    build, env, _slib = _build_lib(tmp_path, PLAIN_LIB, "liba", kind="source")
    assert build.returncode == 0, build.stdout + build.stderr

    build, _env, slib = _build_lib(tmp_path, IMPORTER_LIB, "libb", env=env)
    assert build.returncode == 0, build.stdout + build.stderr

    metadata = LibraryFormat.read_metadata_only(slib)
    assert metadata["units"] == ["libb"]
    assert _names(metadata["public_functions"]) == {"b_two"}
