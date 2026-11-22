"""The `.slib` container since VERSION 6: the metadata, then a source section.

The fixed header is 52 bytes. FLAGS is the second u32 after the magic, and the other
header fields are spares. A length-prefixed SOURCE section follows the metadata, and
nothing follows the source. See `docs/library-format.md`.
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


def test_the_container_version_is_6():
    assert LibraryFormat.VERSION == 6


def test_the_fixed_header_is_still_52_bytes():
    assert LibraryFormat.FIXED_HEADER_SIZE == 52


# --- Round trips ------------------------------------------------------------

def test_a_library_round_trips_its_units(tmp_path):
    path = tmp_path / "srclib.slib"
    LibraryFormat.write(path, {"library_name": "srclib"}, source=SOURCE)

    metadata, source = LibraryFormat.read(path)
    assert metadata["library_name"] == "srclib"
    assert source == SOURCE


def test_a_library_with_no_source_reads_back_an_empty_map(tmp_path):
    path = tmp_path / "nosrc.slib"
    LibraryFormat.write(path, {"library_name": "nosrc"})

    _metadata, source = LibraryFormat.read(path)
    assert source == {}


def test_the_source_section_is_the_last_section(tmp_path):
    path = tmp_path / "last.slib"
    LibraryFormat.write(path, {"library_name": "last"}, source=SOURCE)

    meta_blob = msgpack.packb({"library_name": "last"}, use_bin_type=True)
    src_blob = msgpack.packb(SOURCE, use_bin_type=True)
    raw = path.read_bytes()
    assert len(raw) == LibraryFormat.FIXED_HEADER_SIZE + len(meta_blob) + 8 + len(src_blob)
    assert LibraryFormat.read_source_size(path) == ({"library_name": "last"}, len(src_blob))


def test_the_flags_and_the_spares_are_written_as_zero(tmp_path):
    path = tmp_path / "flags.slib"
    LibraryFormat.write(path, {"library_name": "flags"}, source=SOURCE)

    raw = path.read_bytes()
    assert struct.unpack("<I", raw[16:20])[0] == 6     # VERSION
    assert struct.unpack("<I", raw[20:24])[0] == 0     # FLAGS
    assert raw[24:44] == bytes(20)                     # the spares


# --- Cheap readers skip what they do not need ------------------------------

def test_read_metadata_only_skips_the_source(tmp_path):
    path = tmp_path / "meta.slib"
    LibraryFormat.write(path, {"library_name": "meta"}, source=SOURCE)

    assert LibraryFormat.read_metadata_only(path)["library_name"] == "meta"


# --- Rejections -------------------------------------------------------------

@pytest.mark.parametrize("version", [3, 5])
def test_an_older_container_is_rejected(tmp_path, version):
    path = tmp_path / "old.slib"
    LibraryFormat.write(path, {"library_name": "old"})
    blob = bytearray(path.read_bytes())
    blob[16:20] = struct.pack("<I", version)
    path.write_bytes(bytes(blob))

    with pytest.raises(LibraryError) as excinfo:
        LibraryFormat.read(path)
    assert excinfo.value.code == "CE3509"


def test_a_truncated_source_section_is_reported(tmp_path):
    path = tmp_path / "cut.slib"
    LibraryFormat.write(path, {"library_name": "cut"}, source=SOURCE)
    meta_blob = msgpack.packb({"library_name": "cut"}, use_bin_type=True)
    src_blob = msgpack.packb(SOURCE, use_bin_type=True)
    keep = LibraryFormat.FIXED_HEADER_SIZE + len(meta_blob) + 8 + len(src_blob) // 2
    path.write_bytes(path.read_bytes()[:keep])

    for reader in (LibraryFormat.read, LibraryFormat.read_source_size):
        with pytest.raises(LibraryError) as excinfo:
            reader(path)
        assert excinfo.value.code == "CE3506"
