"""A parameter MODE survives a `.slib` boundary."""
from __future__ import annotations

import struct

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushi_lang.semantics.type_resolution import parse_type_string
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType


# The type-string parser learned peek and poke

@pytest.mark.parametrize("text,mode,referent", [
    ("peek string", BorrowMode.PEEK, BuiltinType.STRING),
    ("poke i32", BorrowMode.POKE, BuiltinType.I32),
])
def test_parse_type_string_reads_a_reference_back(text, mode, referent):
    ty = parse_type_string(text, {}, {})
    assert isinstance(ty, ReferenceType)
    assert ty.mutability is mode
    assert ty.referenced_type == referent


def test_a_reference_type_round_trips_through_its_string_form():
    for mode in (BorrowMode.PEEK, BorrowMode.POKE):
        original = ReferenceType(referenced_type=BuiltinType.STRING, mutability=mode)
        assert parse_type_string(str(original), {}, {}) == original


def test_a_dynamic_array_referent_round_trips():
    ty = parse_type_string("peek i32[]", {}, {})
    assert isinstance(ty, ReferenceType)
    assert str(ty) == "peek i32[]"


# The manifest carries the mode as its own field










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
