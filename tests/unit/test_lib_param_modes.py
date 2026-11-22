"""A parameter MODE survives a `.slib` boundary."""
from __future__ import annotations

import struct

import pytest

from sushi_lang.backend.library_format import LibraryFormat


# A v2 library is rejected, not guessed at

def test_a_v2_library_is_rejected(tmp_path):
    from sushi_lang.backend.library_errors import LibraryError

    path = tmp_path / "old.slib"
    LibraryFormat.write(path, {"name": "old"}, b"")
    blob = bytearray(path.read_bytes())
    blob[16:20] = struct.pack("<I", 2)
    path.write_bytes(bytes(blob))

    with pytest.raises(LibraryError) as excinfo:
        LibraryFormat.read(path)
    assert excinfo.value.code == "CE3509"
