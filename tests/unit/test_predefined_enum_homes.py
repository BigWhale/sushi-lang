"""Every predefined enum but StdError has a HOME module, and the home is a real module.

`docs/design/unit-namespaces.md` section 4 says a name no import brings can sit in no
namespace. #574 (Ruling 3) gives each synthesized enum a module: the import gates the
bare name and the alias holds it. These pin the table against the synthesis and against
the module lists, so an enum added to `register_predefined_enums` without a home, or a
home that names no module, fails here and not at a user's `use`.
"""
from __future__ import annotations

from sushi_lang.semantics.namespaces import GENERIC_UNIT_TYPES, homed_enums
from sushi_lang.semantics.passes.collect import EnumTable
from sushi_lang.semantics.passes.collect.enums import PREDEFINED_ENUM_HOMES, EnumCollector
from sushi_lang.semantics.stdlib_registry import SOURCE_STDLIB_MODULES, get_stdlib_registry
from sushi_lang.internals.report import Reporter


def _synthesized() -> EnumTable:
    from sushi_lang.semantics.passes.collect import (
        GenericEnumTable, GenericStructTable, StructTable)
    collector = EnumCollector(Reporter(), EnumTable(), GenericEnumTable(), StructTable(),
                              GenericStructTable(), set())
    collector.register_predefined_enums()
    return collector.enums


def test_every_synthesized_enum_is_in_the_homes_table():
    table = _synthesized()
    assert set(table.by_name) == set(PREDEFINED_ENUM_HOMES)


def test_every_synthesized_enum_carries_its_stamp():
    table = _synthesized()
    for name, enum in table.by_name.items():
        assert enum.home_module == PREDEFINED_ENUM_HOMES[name], name


def test_std_error_is_the_one_global():
    unhomed = sorted(name for name, home in PREDEFINED_ENUM_HOMES.items() if home is None)
    assert unhomed == ["StdError"]


def test_every_home_is_a_stdlib_module():
    registry = get_stdlib_registry()
    modules = set(SOURCE_STDLIB_MODULES) | set(GENERIC_UNIT_TYPES) | set(registry.get_all_modules())
    for name, home in PREDEFINED_ENUM_HOMES.items():
        if home is not None:
            assert home in modules, f"{name} is homed at <{home}>, which is no module"


def test_a_home_lists_its_enums_as_members():
    table = _synthesized()
    assert homed_enums("io/fs", table) == {"FileMode": "enum"}
    assert homed_enums("io/contracts", table) == {"SeekFrom": "enum"}
    assert homed_enums("io/error", table) == {"FileError": "enum", "IoError": "enum"}
    assert homed_enums("net/error", table) == {"NetError": "enum"}
    assert homed_enums("math", table) == {"MathError": "enum"}
    assert homed_enums("collections/hashmap", table) == {}
