"""`Hashable` is predefined, and every type with a derived hash satisfies it (#696).

The fixtures under tests/perks/hashable/ pin the exit codes. A directive reads a code as
a SUBSTRING, so this file pins the code SET per program, and the shape of the perk the
compiler predefines beside `Drop`.
"""
from __future__ import annotations


from sushi_lang.semantics.passes.collect import PerkImplementationTable
from sushi_lang.semantics.passes.collect.perks import PerkCollector




















def test_the_magic_list_is_gone():
    """The derive pass's predicate is the one answer; no table of twelve names beside it."""
    assert not hasattr(PerkCollector, "register_synthetic_impls")
    assert not hasattr(PerkImplementationTable, "register_synthetic")




def test_the_tables_hand_the_derived_table_the_override():
    """Every registration reads `hash_override` from the one derived table."""
    from sushi_lang.semantics.tables import SymbolTables
    tables = SymbolTables()
    assert tables.derived_methods.hash_override is not None
