"""The consumer refuses a binary .slib whose templates schema is not the current one (decision B).

Version 5 keys every closure record by its unit and gives a source-shipped template
its bindings map (#494, D4). A version-4 library's bare-name records can bind a
template to another unit's body -- a silent wrong answer -- so an old library is
refused with CE3512 and must be rebuilt. Version 6 ships every public perk and every
generic-target perk implementation template (#543); a version-5 library has neither.
A SOURCE library recompiles from its units and reads none of this, so the gate skips it.
"""
import pytest

from sushi_lang.backend.library_errors import LibraryError
from sushi_lang.compiler.pipeline import (
    TEMPLATES_SCHEMA_VERSION,
    _check_library_templates_version,
)


def _metadata(kind: str, version) -> dict:
    return {"kind": kind, "templates": {"version": version}}


def test_a_version_4_binary_library_is_refused():
    with pytest.raises(LibraryError) as exc:
        _check_library_templates_version(_metadata("binary", 4), "old.slib")
    assert exc.value.code == "CE3512"
    assert "templates schema version 4" in str(exc.value)


def test_a_binary_library_with_no_templates_section_is_refused():
    with pytest.raises(LibraryError):
        _check_library_templates_version({"kind": "binary"}, "old.slib")


def test_a_version_5_binary_library_is_refused():
    with pytest.raises(LibraryError) as exc:
        _check_library_templates_version(_metadata("binary", 5), "old.slib")
    assert exc.value.code == "CE3512"


def test_a_version_6_binary_library_is_refused():
    """Version 7 gives a perk record its method signatures (#537)."""
    with pytest.raises(LibraryError) as exc:
        _check_library_templates_version(_metadata("binary", 6), "old.slib")
    assert exc.value.code == "CE3512"


def test_the_current_schema_passes():
    _check_library_templates_version(_metadata("binary", TEMPLATES_SCHEMA_VERSION), "new.slib")
    assert TEMPLATES_SCHEMA_VERSION == 7


def test_a_source_library_is_never_gated():
    _check_library_templates_version(_metadata("source", 4), "src.slib")
