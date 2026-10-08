"""The predefined perks, and the override each derived contract reads (#696).

The fixtures under tests/perks/hashable/ and tests/perks/predefined_contracts/ pin the
diagnostics. This module checks the registry: which perks the compiler declares, what
their contracts say, and that the symbol tables give the derived table an override
predicate for each derived contract.
"""
from __future__ import annotations

from sushi_lang.semantics.passes.collect.perks import PerkCollector
from sushi_lang.semantics.typesys import BuiltinType, ReceiverType


def _predefined():
    collector = PerkCollector.__new__(PerkCollector)
    return {perk.name: perk for perk in collector._predefined_perks()}


def test_the_predefined_set_is_exactly_six():
    assert set(_predefined()) == {"Drop", "Hashable", "Eq", "Ord", "Display", "Clone"}


def test_eq_and_ord_name_the_receiver_and_display_takes_nothing():
    perks = _predefined()
    (eq,) = perks["Eq"].methods
    (compare,) = perks["Ord"].methods
    (to_str,) = perks["Display"].methods
    assert (eq.name, [type(p.ty) for p in eq.params], eq.ret) == (
        "eq", [ReceiverType], BuiltinType.BOOL)
    assert (compare.name, [type(p.ty) for p in compare.params], compare.ret) == (
        "compare", [ReceiverType], BuiltinType.I32)
    assert (to_str.name, to_str.params, to_str.ret) == ("to_str", [], BuiltinType.STRING)


def test_clone_names_the_receiver_as_its_answer():
    (clone,) = _predefined()["Clone"].methods
    assert (clone.name, clone.params, type(clone.ret)) == ("clone", [], ReceiverType)


def test_the_tables_hand_the_derived_table_the_overrides():
    """Every derived contract reads its override from the one derived table."""
    from sushi_lang.semantics.tables import SymbolTables
    tables = SymbolTables()
    assert tables.derived_methods.hash_override is not None
    assert set(tables.derived_methods.overrides) == {"Eq", "Ord", "Display"}
