"""`--lib-kind source` ships the library's units as text.

Production only: nothing consumes a source library yet. What is asserted here is what
lands in the container, and that a source library stops being platform-bound.
See `docs/design/libraries.md` section 4.1.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from sushi_lang.backend.library_errors import LibraryError
from sushi_lang.backend.library_format import LibraryFormat


LIB_MAIN = """\
use "helper"

public fn twice(i32 n) i32:
    return Result.Ok(bump(n)?? + n - 1)
"""

LIB_HELPER = """\
fn secret(i32 n) i32:
    return Result.Ok(n + 1)

public fn bump(i32 n) i32:
    return Result.Ok(secret(n)??)
"""

ITER_LIB = """\
use <collections/iter>

public fn one() i32:
    return Result.Ok(1)
"""


def _build(tmp_path, *extra_args, sources: dict[str, str] | None = None):
    """Compile a library and return (CompletedProcess, output path)."""
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    files = sources if sources is not None else {"srclib": LIB_MAIN, "helper": LIB_HELPER}
    for name, text in files.items():
        (tmp_path / f"{name}.sushi").write_text(text, encoding="utf-8")
    main = next(iter(files))
    out = tmp_path / f"{main}.slib"
    result = subprocess.run(
        ["sushic", "--lib", "--lib-version", "1.0.0",
         str(tmp_path / f"{main}.sushi"), "-o", str(out), *extra_args],
        cwd=tmp_path, capture_output=True, text=True)
    return result, out


# --- What a source library contains -----------------------------------------

@pytest.fixture(scope="module")
def source_lib(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("srclib")
    result, out = _build(tmp_path, "--lib-kind", "source")
    assert result.returncode == 0, result.stdout + result.stderr
    return LibraryFormat.read_source_only(out) + (out,)


def test_the_manifest_says_it_is_a_source_library(source_lib):
    metadata, _source, _out = source_lib
    assert metadata["kind"] == "source"


def test_every_unit_ships_its_whole_text(source_lib):
    _metadata, source, _out = source_lib
    assert source["srclib"] == LIB_MAIN
    assert source["helper"] == LIB_HELPER


def test_a_private_declaration_ships_too(source_lib):
    # There is no export closure on this path: everything ships, so a private helper
    # is reachable because it is compiled, not because the producer predicted the need.
    _metadata, source, _out = source_lib
    assert "fn secret" in source["helper"]


def test_the_unit_list_matches_the_source_section(source_lib):
    metadata, source, _out = source_lib
    assert sorted(metadata["units"]) == sorted(source)


def test_a_source_library_carries_no_bitcode(source_lib):
    _metadata, _source, out = source_lib
    _meta, bitcode = LibraryFormat.read(out)
    assert bitcode == b""


def test_a_bundled_stdlib_module_does_not_ship(tmp_path):
    # The consumer has its own copy of <collections/iter>; shipping ours would put a
    # second definition of every symbol into their build.
    result, out = _build(tmp_path, "--lib-kind", "source", sources={"iterlib": ITER_LIB})
    assert result.returncode == 0, result.stdout + result.stderr

    metadata, source = LibraryFormat.read_source_only(out)
    assert "collections/iter" not in source
    assert "collections/iter" not in metadata["units"]
    assert "iterlib" in source


# --- The other two kinds ----------------------------------------------------

def test_binary_is_still_the_default(tmp_path):
    # Phase 4 flips this once a consumer can compile a source library.
    result, out = _build(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

    metadata, source = LibraryFormat.read_source_only(out)
    assert metadata["kind"] == "binary"
    assert source == {}


def test_a_hybrid_carries_source_and_bitcode(tmp_path):
    result, out = _build(tmp_path, "--lib-kind", "hybrid")
    assert result.returncode == 0, result.stdout + result.stderr

    metadata, source = LibraryFormat.read_source_only(out)
    _meta, bitcode = LibraryFormat.read(out)
    assert metadata["kind"] == "hybrid"
    assert source["srclib"] == LIB_MAIN
    assert bitcode != b""


def test_an_unknown_kind_is_rejected(tmp_path):
    result, _out = _build(tmp_path, "--lib-kind", "nonsense")
    assert result.returncode != 0


# --- A source library is not platform-bound ---------------------------------

def _load(metadata, kind):
    from sushi_lang.compiler.pipeline import _check_library_platform

    _check_library_platform({**metadata, "kind": kind}, "lib/x")


FOREIGN = {"library_name": "x", "platform": "definitely-not-this-one"}


def test_a_source_library_loads_on_any_platform():
    # The single conditional that A3 was really asking for: the platform field is
    # meaningless when nothing in the container is machine code.
    _load(FOREIGN, "source")


@pytest.mark.parametrize("kind", ["binary", "hybrid"])
def test_a_library_carrying_bitcode_is_still_platform_bound(kind):
    with pytest.raises(LibraryError) as excinfo:
        _load(FOREIGN, kind)
    assert excinfo.value.code == "CE3504"


def test_a_matching_platform_loads_whatever_the_kind():
    from sushi_lang.backend.platform_detect import current_platform_name

    for kind in ("source", "binary", "hybrid"):
        _load({"library_name": "x", "platform": current_platform_name()}, kind)
