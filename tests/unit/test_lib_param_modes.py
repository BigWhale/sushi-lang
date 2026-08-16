"""A parameter MODE survives a `.slib` boundary.

Before the mode became a declared field, a library could not state a borrow. The
manifest serialized `peek string` into the type string correctly, but the consumer's
`parse_type_string` had no reference arm, so it read back `UnknownType("peek string")`
and the mode was lost. A `nom` had nowhere to be written at all.

The format version is 3 because of this field. A v2 library states no mode, so its
parameters cannot be told apart from unmarked ones; CE3509 rejects it rather than guess.

See docs/design/borrow-model.md sections 5 and 10.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sushi_lang.backend.library_format import LibraryFormat
from sushi_lang.semantics.param_modes import ParamMode, param_mode
from sushi_lang.semantics.type_resolution import parse_type_string
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType


# --------------------------------------------------------------------------- #
# The type-string parser learned peek and poke
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# The manifest carries the mode as its own field
# --------------------------------------------------------------------------- #

MODE_LIB = """\
use <collections/strings>

public fn borrows(string s) i32:
    return Result.Ok(s.len())

public fn eats(nom string s) i32:
    return Result.Ok(s.len() + 1)

public fn reads(peek i32 n) i32:
    return Result.Ok(n)

public fn writes(poke i32 n) ~:
    n := 9
    return Result.Ok(~)
"""

EXPECTED_MODES = {
    "borrows": [ParamMode.BORROW],
    "eats": [ParamMode.NOM],
    "reads": [ParamMode.PEEK],
    "writes": [ParamMode.POKE],
}


@pytest.fixture(scope="module")
def mode_manifest(tmp_path_factory):
    """Build a library declaring one function per mode, and read its manifest back."""
    import subprocess
    import shutil
    if shutil.which("sushic") is None:
        pytest.skip("sushic not on PATH")
    tmp_path = tmp_path_factory.mktemp("modelib")
    src = tmp_path / "modelib.sushi"
    src.write_text(MODE_LIB, encoding="utf-8")
    out = tmp_path / "modelib.slib"
    result = subprocess.run(
        ["sushic", "--lib", str(src), "-o", str(out)],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    metadata, _bitcode = LibraryFormat.read(out)
    return metadata


@pytest.mark.parametrize("fn_name", sorted(EXPECTED_MODES))
def test_the_manifest_records_the_declared_mode(mode_manifest, fn_name):
    funcs = {f["name"]: f for f in mode_manifest["public_functions"]}
    assert fn_name in funcs, sorted(funcs)
    recorded = [p.get("mode") for p in funcs[fn_name]["params"]]
    assert recorded == [m.value for m in EXPECTED_MODES[fn_name]]


@pytest.mark.parametrize("fn_name", sorted(EXPECTED_MODES))
def test_the_consumer_reads_the_same_mode_back(mode_manifest, fn_name):
    from sushi_lang.semantics.library_registry import LibraryRegistry
    registry = LibraryRegistry()
    registry.register_library(Path("modelib.slib"), mode_manifest)
    sig = dict(registry.get_all_functions())[fn_name]
    assert [param_mode(p) for p in sig.params] == EXPECTED_MODES[fn_name]


# --------------------------------------------------------------------------- #
# A v2 library is rejected, not guessed at
# --------------------------------------------------------------------------- #

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
