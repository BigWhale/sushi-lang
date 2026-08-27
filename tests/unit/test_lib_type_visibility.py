"""A `.slib` exports the types, enums and constants a library MARKS, and no others.

`docs/design/visibility.md` section 7. Three extractors had no gate at all, so a decoder
detail shipped as frozen API with its whole field layout printed by `--lib-info` (D8), and
a bundled module's constants leaked into a library's manifest because the constant
extractor iterated every unit instead of the library's own.

What is kept is NAMED, in the same `not_exported` key a kept function uses, so a consumer
that writes the name hears CE3005 -- "private struct, defined in that library" -- and not
CE2001 "unknown type" about a type the library does define.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat


needs_sushic = pytest.mark.skipif(shutil.which("sushic") is None,
                                  reason="sushic not on PATH")

MIXED_LIB = """\
public struct Line:
    i32 length

struct Point:
    i32 x
    i32 y

public enum Mood:
    Calm
    Cross

enum Secret:
    Hidden

public const i32 LIMIT = 100

const i32 QUIET = 7

public fn origin_sum() i32:
    let Point p = Point(0, 0)
    return Result.Ok(p.x + p.y + QUIET)
"""

BUNDLED_LIB = """\
use <encoding/msgpack>

public fn decode_len(u8[] buf) i32:
    return Result.Ok(buf.len())
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_lib(tmp_path: Path, source: str, name: str, kind: str = "source"):
    libs_dir = tmp_path / "libs"
    libs_dir.mkdir(exist_ok=True)
    lib_src = tmp_path / f"{name}.sushi"
    _write(lib_src, source)
    slib = libs_dir / f"{name}.slib"
    result = subprocess.run(
        ["sushic", "--lib", "--lib-kind", kind, "--lib-version", "1.2.0",
         str(lib_src), "-o", str(slib)],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    env = {**os.environ, "SUSHI_LIB_PATH": str(libs_dir), "NO_COLOR": "1"}
    return env, slib


def _consume(tmp_path: Path, env: dict, program: str, name: str = "prog") -> str:
    project = tmp_path / name
    _write(project / "main.sushi", program)
    result = subprocess.run(["sushic", "main.sushi", "-o", "out"],
                            cwd=project, capture_output=True, text=True, env=env)
    return result.stdout + result.stderr


def _kept(slib: Path) -> dict[str, str]:
    metadata = LibraryFormat.read_metadata_only(slib)
    return {r["name"]: r["kind"] for r in metadata.get("not_exported", [])}


# --- what the manifest carries -----------------------------------------------------

@needs_sushic
def test_only_marked_types_and_constants_are_exported(tmp_path):
    _env, slib = _build_lib(tmp_path, MIXED_LIB, "mixedlib")
    metadata = LibraryFormat.read_metadata_only(slib)

    assert [s["name"] for s in metadata["structs"]] == ["Line"]
    assert [e["name"] for e in metadata["enums"]] == ["Mood"]
    assert [c["name"] for c in metadata["public_constants"]] == ["LIMIT"]


@needs_sushic
def test_what_is_kept_is_named_with_its_kind(tmp_path):
    _env, slib = _build_lib(tmp_path, MIXED_LIB, "keptkinds")
    assert _kept(slib) == {"Point": "struct", "Secret": "enum", "QUIET": "constant"}


CLOSURE_LIB = """\
struct Acc:
    i32 total

const i32 SCALE = 3

fn scale_up(i32 x) i32:
    let Acc a = Acc(x * SCALE)
    return Result.Ok(a.total)

public fn through@(T)(nom T x, i32 n) i32:
    return Result.Ok(scale_up(n)??)
"""


@needs_sushic
def test_a_private_type_the_closure_needs_travels_as_source(tmp_path):
    """A template body names it, so it ships -- beside the closure's constants.

    It is NOT in the public index, which is what the marker gates, and it is not in
    `not_exported` either: each private is named in exactly one of the two places.
    """
    _env, slib = _build_lib(tmp_path, CLOSURE_LIB, "closlib")
    metadata = LibraryFormat.read_metadata_only(slib)
    templates = metadata["templates"]

    assert metadata["structs"] == []
    assert [r["name"] for r in templates["private_types"]] == ["Acc"]
    assert templates["closure_summary"]["private_types"] == ["Acc"]
    assert "Acc" not in _kept(slib)
    assert "SCALE" not in _kept(slib)


@needs_sushic
def test_a_consumer_still_cannot_name_a_shipped_private_type(tmp_path):
    env, _slib = _build_lib(tmp_path, CLOSURE_LIB, "closname")
    out = _consume(tmp_path, env, """\
use <lib/closname>

fn main() i32:
    let Acc a = Acc(1)
    println("{a.total}")
    return Result.Ok(0)
""")
    assert "CE3005" in out, out
    assert "private struct 'Acc'" in out, out


@needs_sushic
def test_a_bundled_modules_constants_are_not_this_librarys(tmp_path):
    """The constant extractor iterated every unit, `<encoding/msgpack>` included."""
    _env, slib = _build_lib(tmp_path, BUNDLED_LIB, "bundconst")
    metadata = LibraryFormat.read_metadata_only(slib)
    assert metadata["public_constants"] == []
    assert "MP_NIL" not in _kept(slib)


@needs_sushic
def test_the_protocol_version_says_the_manifest_changed(tmp_path):
    _env, slib = _build_lib(tmp_path, MIXED_LIB, "protolib")
    metadata = LibraryFormat.read_metadata_only(slib)
    assert metadata["sushi_lib_version"] == "2.1"


# --- what a consumer hears ---------------------------------------------------------

@needs_sushic
def test_a_kept_struct_is_private_and_not_unknown(tmp_path):
    env, _slib = _build_lib(tmp_path, MIXED_LIB, "structlib", kind="binary")
    out = _consume(tmp_path, env, """\
use <lib/structlib>

fn main() i32:
    let Point p = Point(1, 2)
    println("{p.x}")
    return Result.Ok(0)
""")
    assert "CE3005" in out, out
    assert "private struct 'Point'" in out, out
    assert "CE2001" not in out, out


@needs_sushic
def test_a_marked_type_still_crosses(tmp_path):
    env, _slib = _build_lib(tmp_path, MIXED_LIB, "openlib", kind="binary")
    out = _consume(tmp_path, env, """\
use <lib/openlib>

fn main() i32:
    let Line l = Line(9)
    println("{l.length}")
    return Result.Ok(0)
""")
    assert "error" not in out, out


@needs_sushic
def test_a_source_librarys_marked_constant_crosses_and_its_private_one_does_not(tmp_path):
    """A SOURCE library, because its units are ordinary units at the consumer.

    A BINARY library's constants reach a consumer only through the export closure -- the
    ones a public generic's body needs -- so no constant of one is nameable today,
    whether it is marked or not. The manifest gate above is what matters there: a private
    constant is no longer handed out as API. Naming one is held back deliberately: a
    private-versus-unknown wording split would answer half a question while a public
    constant of the same library is equally unreachable.
    """
    env, _slib = _build_lib(tmp_path, MIXED_LIB, "srcconst", kind="source")
    good = _consume(tmp_path, env, """\
use <lib/srcconst>

fn main() i32:
    println("{LIMIT}")
    return Result.Ok(0)
""", name="good")
    assert "error" not in good, good

    bad = _consume(tmp_path, env, """\
use <lib/srcconst>

fn main() i32:
    println("{QUIET}")
    return Result.Ok(0)
""", name="bad")
    assert "CE3005" in bad, bad
    assert "private constant 'QUIET'" in bad, bad


# --- what the report shows ---------------------------------------------------------

@needs_sushic
def test_lib_info_does_not_hand_out_a_private_field_layout(tmp_path):
    """D8: the report listed a private struct as API, with every field."""
    _env, slib = _build_lib(tmp_path, MIXED_LIB, "infolib")
    result = subprocess.run(["sushic", "--lib-info", str(slib)],
                            capture_output=True, text=True,
                            env={**os.environ, "NO_COLOR": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    printed = result.stdout + result.stderr
    assert "Line" in printed
    assert "Point" not in printed, printed
    assert "Secret" not in printed, printed
    assert "QUIET" not in printed, printed
