"""Unit tests for the .slib generic-template codec (Phase 2, P2-T1 + P2-T2)."""
from __future__ import annotations

from types import SimpleNamespace

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.library_registration import LibraryRegistration
from sushi_lang.semantics.tables import SymbolTables
from sushi_lang.semantics.library_templates import (
    impl_method_symbol,
)
































# P2-T4: consumer-side registration of library generic templates.



def _registration_with_loaded_libraries(loaded: dict) -> LibraryRegistration:
    """Build a bare registration wired with a fake library linker + empty tables."""
    reporter = Reporter(source="", filename="consumer")
    fake_linker = SimpleNamespace(loaded_libraries=loaded)
    return LibraryRegistration(reporter, SymbolTables(), fake_linker, None)








def test_register_library_generic_function_guards_missing_templates():
    """A manifest without a templates section registers nothing and does not crash."""
    libraries = _registration_with_loaded_libraries(
        {"mathlib": {"library_name": "mathlib"}}
    )

    libraries._register_generic_functions(set())

    assert libraries.tables.generic_funcs.by_name == {}


# Phase 2 Step A: perk DEFINITION shipping (definitions only, no impls).
















def test_seed_library_perks_guards_missing_templates():
    """A manifest without a templates section seeds nothing and does not crash."""
    libraries = _registration_with_loaded_libraries(
        {"mathlib": {"library_name": "mathlib"}}
    )
    from sushi_lang.semantics.passes.collect import PerkTable
    table = PerkTable()

    libraries.seed_perks(table)

    assert table.by_name == {}


# P2-5 Phase 1 (C3): generic STRUCT / ENUM templates.




















# C4a: concrete perk-impl shipping (templates.perk_impls)









def test_impl_method_symbol_matches_extension_naming():
    """The manifest symbol matches the backend's extension-method mangling
    (get_extension_method_name): sanitized type name + "_" + method name.
    """
    assert impl_method_symbol("i32", "doubled") == "i32_doubled"
    assert impl_method_symbol("Box<i32>", "unwrap") == "Box__i32_unwrap"
    assert impl_method_symbol("HashMap<string, i32>", "get") == "HashMap__string_i32_get"














