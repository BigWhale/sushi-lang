"""A `public var` travels through a binary `.slib` as external storage.

The record mirrors `public_constants` -- the type and the declaration's source -- and
adds the `link_symbol` the consumer declares, because the storage is defined once, in
the library's bitcode (BUGS.md ruling on #546, the Manifest item). `--lib-info` prints
the kind that was written.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from sushic_path import SUSHIC, needs_sushic

LIB = """\
##: A library with storage. :##

##: How many times `hit()` ran. :##
public var i32 hits = 0

var i32 secret = 40

##: One more hit. :##
public fn hit() ~:
    hits := hits + 1
    return Result.Ok(~)
"""


def _build(tmp_path: Path, kind: str) -> Path:
    src = tmp_path / "storage.sushi"
    src.write_text(LIB, encoding="utf-8")
    out = tmp_path / f"storage_{kind}.slib"
    result = subprocess.run(
        [SUSHIC, "--lib", "--lib-version", "0.0.0", "--lib-kind", kind,
         str(src), "-o", str(out)],
        capture_output=True, text=True, env={**os.environ, "NO_COLOR": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return out


def _manifest(slib: Path) -> dict:
    from sushi_lang.backend.library_format import LibraryFormat
    metadata, _payload = LibraryFormat.read(slib)
    return metadata


@needs_sushic
@pytest.mark.parametrize("kind", ["source", "binary"])
def test_a_public_var_is_recorded_with_its_link_symbol(tmp_path, kind):
    manifest = _manifest(_build(tmp_path, kind))
    records = manifest["public_variables"]
    assert [r["name"] for r in records] == ["hits"]
    record = records[0]
    assert record["unit"] == "storage"
    assert record["type"] == "i32"
    assert record["source"].startswith("public var i32 hits = 0")
    assert record["link_symbol"] == "storage$hits"
    assert "hits" not in {r["name"] for r in manifest["public_constants"]}


@needs_sushic
def test_a_private_var_is_named_as_kept(tmp_path):
    manifest = _manifest(_build(tmp_path, "binary"))
    kept = {r["name"]: r["kind"] for r in manifest["not_exported"]}
    assert kept["secret"] == "variable"


@needs_sushic
def test_lib_info_prints_the_var_with_its_kind(tmp_path):
    slib = _build(tmp_path, "binary")
    result = subprocess.run(
        [SUSHIC, "--lib-info", str(slib)],
        capture_output=True, text=True, env={**os.environ, "NO_COLOR": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "var i32 hits" in result.stdout
    assert "const i32 hits" not in result.stdout
