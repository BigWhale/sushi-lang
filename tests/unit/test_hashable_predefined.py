"""`Hashable` is predefined, and every type with a derived hash satisfies it (#696).

The fixtures under tests/perks/hashable/ pin the diagnostics. This module checks that
the symbol tables give the derived table its `Hashable` override.
"""
from __future__ import annotations


def test_the_tables_hand_the_derived_table_the_override():
    """Every registration reads `hash_override` from the one derived table."""
    from sushi_lang.semantics.tables import SymbolTables
    tables = SymbolTables()
    assert tables.derived_methods.hash_override is not None
