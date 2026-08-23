"""The `.slib` container at VERSION 4: a source section beside the bitcode.

Version 4 claims two of the reserved header fields as FLAGS and KIND, so the fixed
52-byte header does not change size, and adds a length-prefixed SOURCE section between
the metadata and the bitcode. See `docs/design/libraries.md` section 2.
"""
from __future__ import annotations

import struct

import msgpack
import pytest

from sushi_lang.backend.library_errors import LibraryError
from sushi_lang.backend.library_format import LibraryFormat


SOURCE = {
    "greet": "public fn greet(string who) string:\n    return Result.Ok(who)\n",
    "helper": "fn twice(i32 n) i32:\n    return Result.Ok(n * 2)\n",
}


def test_the_container_version_is_4():
    assert LibraryFormat.VERSION == 4


def test_the_fixed_header_is_still_52_bytes():
    # FLAGS and KIND reuse SPARE_1 and SPARE_2 rather than growing the header.
    assert LibraryFormat.FIXED_HEADER_SIZE == 52


# --- Round trips ------------------------------------------------------------

def test_a_source_library_round_trips_its_units(tmp_path):
    path = tmp_path / "srclib.slib"
    LibraryFormat.write(path, {"library_name": "srclib", "kind": "source"},
                        b"", source=SOURCE)

    metadata, source = LibraryFormat.read_source_only(path)
    assert metadata["kind"] == "source"
    assert source == SOURCE


def test_a_binary_library_still_round_trips_metadata_and_bitcode(tmp_path):
    path = tmp_path / "binlib.slib"
    bitcode = b"\x01\x02\x03\x04"
    LibraryFormat.write(path, {"library_name": "binlib", "kind": "binary"}, bitcode)

    metadata, read_back = LibraryFormat.read(path)
    assert metadata["library_name"] == "binlib"
    assert read_back == bitcode


def test_a_hybrid_carries_both_sections(tmp_path):
    path = tmp_path / "both.slib"
    bitcode = b"\xde\xad\xbe\xef"
    LibraryFormat.write(path, {"library_name": "both", "kind": "hybrid"},
                        bitcode, source=SOURCE)

    _meta, read_bitcode = LibraryFormat.read(path)
    _meta2, read_source = LibraryFormat.read_source_only(path)
    assert read_bitcode == bitcode
    assert read_source == SOURCE


def test_a_library_with_no_source_reads_back_an_empty_map(tmp_path):
    path = tmp_path / "nosrc.slib"
    LibraryFormat.write(path, {"library_name": "nosrc", "kind": "binary"}, b"\x00")

    _metadata, source = LibraryFormat.read_source_only(path)
    assert source == {}


# --- The header mirrors the manifest ---------------------------------------

@pytest.mark.parametrize("kind,code", [("source", 1), ("binary", 2), ("hybrid", 3)])
def test_the_kind_header_field_mirrors_the_manifest(tmp_path, kind, code):
    path = tmp_path / f"{kind}.slib"
    LibraryFormat.write(path, {"library_name": kind, "kind": kind}, b"", source=SOURCE)

    raw = path.read_bytes()
    assert struct.unpack("<I", raw[24:28])[0] == code


def test_a_manifest_with_no_kind_is_written_as_binary(tmp_path):
    # Keeps every pre-v4 caller that passes only metadata and bitcode working.
    path = tmp_path / "implicit.slib"
    LibraryFormat.write(path, {"library_name": "implicit"}, b"")

    raw = path.read_bytes()
    assert struct.unpack("<I", raw[24:28])[0] == 2


def test_the_compression_flag_is_always_written_as_zero(tmp_path):
    path = tmp_path / "flags.slib"
    LibraryFormat.write(path, {"library_name": "flags", "kind": "source"},
                        b"", source=SOURCE)

    raw = path.read_bytes()
    assert struct.unpack("<I", raw[16:20])[0] == 4     # VERSION
    assert struct.unpack("<I", raw[20:24])[0] == 0     # FLAGS


# --- Cheap readers skip what they do not need ------------------------------

def test_read_metadata_only_skips_both_later_sections(tmp_path):
    path = tmp_path / "meta.slib"
    LibraryFormat.write(path, {"library_name": "meta", "kind": "hybrid"},
                        b"\x07" * 64, source=SOURCE)

    assert LibraryFormat.read_metadata_only(path)["library_name"] == "meta"


# --- Rejections -------------------------------------------------------------

def test_a_v3_library_is_rejected(tmp_path):
    path = tmp_path / "old.slib"
    LibraryFormat.write(path, {"library_name": "old"}, b"")
    blob = bytearray(path.read_bytes())
    blob[16:20] = struct.pack("<I", 3)
    path.write_bytes(bytes(blob))

    with pytest.raises(LibraryError) as excinfo:
        LibraryFormat.read(path)
    assert excinfo.value.code == "CE3509"


def test_a_truncated_source_section_is_reported(tmp_path):
    path = tmp_path / "cut.slib"
    LibraryFormat.write(path, {"library_name": "cut", "kind": "source"},
                        b"", source=SOURCE)
    # Cut INTO the source blob. Chopping the tail would only remove the bitcode
    # framing, which read_source_only never reaches.
    meta_blob = msgpack.packb({"library_name": "cut", "kind": "source"}, use_bin_type=True)
    src_blob = msgpack.packb(SOURCE, use_bin_type=True)
    keep = LibraryFormat.FIXED_HEADER_SIZE + len(meta_blob) + 8 + len(src_blob) // 2
    path.write_bytes(path.read_bytes()[:keep])

    with pytest.raises(LibraryError) as excinfo:
        LibraryFormat.read_source_only(path)
    assert excinfo.value.code == "CE3506"
