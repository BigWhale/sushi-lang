"""What the `libraries` step files, and what it needs to file it (#707).

Two rules of `semantics/library_registration.py`, neither of which a `.sushi` fixture
can reach. The first is an invariant: a loaded library means a registry, so the registry
arms have one reader and no fallback. The second is a record: a private type the export
closure ships is remembered as a PRIVATE declaration of its library, and the kind of the
record is the kind of the declaration -- a generic struct is a struct.

The manifests here are hand written, because the fault is in the reading and not in the
producing: a library built for it would only say the same thing more slowly.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.library_registration import LibraryRegistration
from sushi_lang.semantics.tables import SymbolTables


class _Linker:
    """The `LoadedLibraries` Protocol: the manifests and nothing else."""

    def __init__(self, manifests: dict) -> None:
        self.loaded_libraries = manifests


def _manifest(private_types: list[dict]) -> dict:
    return {
        "library_name": "probe",
        "library_path": "probe",
        "templates": {"version": 7, "private_types": private_types},
    }


def _register(manifests: dict) -> tuple[LibraryRegistration, SymbolTables]:
    tables = SymbolTables()
    registration = LibraryRegistration(
        Reporter(), tables, _Linker(manifests), None)
    registration.register([])
    return registration, tables


def test_a_linker_always_brings_a_registry():
    """The invariant the registry arms rest on: a manifest is read one way."""
    registration, _tables = _register({"probe": _manifest([])})

    assert registration.registry is not None


def test_no_linker_leaves_no_registry():
    """The other half: nothing loaded, nothing built, nothing registered."""
    tables = SymbolTables()
    registration = LibraryRegistration(Reporter(), tables, None, None)
    registration.register([])

    assert registration.registry is None


@pytest.mark.parametrize("name, kind, source", [
    ("Plain", "struct", "struct Plain:\n    i32 x\n"),
    ("Mood", "enum", "enum Mood:\n    Calm\n"),
    ("Cell", "struct", "struct Cell@(T):\n    T item\n"),
    ("Slot", "enum", "enum Slot@(T):\n    Full(T)\n    Empty\n"),
])
def test_a_shipped_private_type_is_recorded_under_its_own_kind(name, kind, source):
    """A GENERIC one lands in neither concrete table, and it is still what it is."""
    _registration, tables = _register({"probe": _manifest(
        [{"name": name, "unit": "probe", "source": source}])})

    recorded = [origin for one in ("struct", "enum")
                for origin in tables.visibility.origins(one, name)]

    assert [(o.kind, o.is_public, o.unit_name) for o in recorded] == \
        [(kind, False, "probe")]
