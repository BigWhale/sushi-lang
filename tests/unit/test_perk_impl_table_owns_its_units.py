"""Which unit owns a perk implementation is a fact of the table, not of a collector.

Decision 11 of `docs/design/visibility.md` keeps the perk-implementation override: a
consumer's `extend X with P` wins over a library's, which
`tests/libs/test_lib_perk_impl_local_override.sushi` asserts with an observable result.
The record that answers "which unit declared this implementation" used to live in a
collector-private dict, so the only thing that could read it was the collector itself.
It sits beside `implementations` / `by_type` / `by_perk` now, and it survives the merge.
"""
from __future__ import annotations

from sushi_lang.semantics.ast import ExtendWithDef
from sushi_lang.semantics.passes.collect import PerkImplementationTable
from sushi_lang.semantics.symbol_merger import SymbolTableMerger


def _impl(perk_name: str) -> ExtendWithDef:
    return ExtendWithDef(target_type=None, perk_name=perk_name, methods=[], loc=None)


def test_register_records_the_declaring_unit():
    table = PerkImplementationTable()
    assert table.register(_impl("Loud"), "Box", unit_name="main")
    assert table.owner("Box", "Loud") == "main"


def test_an_unregistered_pair_has_no_owner():
    table = PerkImplementationTable()
    assert table.owner("Box", "Loud") is None


def test_replace_takes_over_the_pair_and_returns_the_displaced_one():
    table = PerkImplementationTable()
    first = _impl("Loud")
    table.register(first, "Box", unit_name="lib/loudlib/loudlib")
    second = _impl("Loud")
    assert table.replace(second, "Box", unit_name="main") is first
    assert table.implementations[("Box", "Loud")] is second
    assert table.owner("Box", "Loud") == "main"


def test_the_owner_survives_the_merge():
    unit_table = PerkImplementationTable()
    unit_table.register(_impl("Loud"), "Box", unit_name="main")
    global_table = PerkImplementationTable()
    SymbolTableMerger()._merge_perk_impls(unit_table, global_table)
    assert global_table.owner("Box", "Loud") == "main"


def test_a_synthetic_implementation_has_no_owner():
    """Nothing declared `i32 with Hashable`, so no unit owns it."""
    table = PerkImplementationTable()
    assert table.register_synthetic("i32", "Hashable")
    assert table.owner("i32", "Hashable") is None
