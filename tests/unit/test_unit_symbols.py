"""The `<unit>$<name>` link symbol, and the manifest field that records it.

Two units may each declare a private `helper`, and the monolithic build path puts every
unit into one `ir.Module`, where an `internal` symbol collides exactly as an `external`
one does. The symbol therefore carries the declaring unit
(`docs/design/unit-namespaces.md` section 9).
"""
from __future__ import annotations



from sushi_lang.semantics.unit_symbols import (
    UNIT_SEP, UnitKeyedSymbols, mangle_unit_symbol,
)


# --- the scheme ------------------------------------------------------------------

def test_a_unit_prefixes_the_name():
    assert mangle_unit_symbol("main", "helper") == "main$helper"


def test_every_slash_in_the_unit_name_becomes_a_separator():
    assert mangle_unit_symbol("collections/iter", "next") == "collections$iter$next"
    assert mangle_unit_symbol("lib/foo/bar", "eat") == "lib$foo$bar$eat"


def test_main_is_exempt():
    """The linker needs the name, and one program declares one `main`."""
    assert mangle_unit_symbol("anything", "main") == "main"


def test_no_unit_means_no_prefix():
    """A monomorphized instance and a lifted lambda belong to no unit."""
    assert mangle_unit_symbol(None, "identity__i32") == "identity__i32"


def test_the_separator_lies_outside_every_other_symbol_alphabet():
    """Invariant (D)'s neighbour: the prefix cannot occur in an unprefixed symbol.

    An identifier and a sanitized type argument are [A-Za-z0-9_], and the pack marker's
    separator is ".". A `$` in a symbol therefore always means "the unit ends here".
    """
    from sushi_lang.semantics.generics.name_mangling import mangle_function_name
    from sushi_lang.semantics.typesys import BuiltinType, DynamicArrayType

    produced = [
        mangle_function_name("identity", (BuiltinType.I32,)),
        mangle_function_name("pair", (BuiltinType.STRING, BuiltinType.F64)),
        mangle_function_name("hold", (DynamicArrayType(BuiltinType.I32),)),
        mangle_function_name("shout", (), pack_arity=3),
        mangle_function_name("shout", (BuiltinType.BOOL,), pack_arity=0),
    ]
    for symbol in produced:
        assert UNIT_SEP not in symbol, symbol


# --- the index that reads a symbol back ------------------------------------------

def test_the_asking_unit_answers_first():
    """`UnitKeyedSymbols.lookup` is `FunctionTable.lookup`'s rule over any value."""
    table: UnitKeyedSymbols[str] = UnitKeyedSymbols()
    table.declare("helper", "left", unit="helpers/left")
    table.declare("helper", "right", unit="helpers/right")

    assert table.lookup("helper", "helpers/left") == "left"
    assert table.lookup("helper", "helpers/right") == "right"
    # A third unit reads the flat view, which is FIRST-wins.
    assert table.lookup("helper", "main") == "left"
    assert table.lookup("helper") == "left"


def test_a_dedup_guard_asks_for_the_units_own_declaration():
    """`declared` never falls back: two units are two declarations, not one."""
    table: UnitKeyedSymbols[str] = UnitKeyedSymbols()
    table.declare("helper", "left", unit="helpers/left")

    assert table.declared("helper", "helpers/left") == "left"
    assert table.declared("helper", "helpers/right") is None


def test_a_symbol_with_no_unit_lives_in_the_flat_view_alone():
    table: UnitKeyedSymbols[str] = UnitKeyedSymbols()
    table.declare("identity__i32", "instance")

    assert table.get("identity__i32") == "instance"
    assert table.by_unit == {}


# --- what a `.slib` records ------------------------------------------------------











