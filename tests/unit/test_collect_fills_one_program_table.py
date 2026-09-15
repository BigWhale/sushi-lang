"""The collect pass fills ONE set of tables, and those tables are the program's.

`_check_multi_file` builds one `CollectorPass` and runs it once per unit. The
collector's tables are instance fields and nothing resets them, so each run adds to
what the runs before it left. A merger that copied the result into a second set
therefore replayed the whole accumulation once per unit -- a cost triangular in the
unit count -- and it copied 13 of the 19 fields, with `externals` repaired by hand
afterwards.

The collect pass owns the tables and the analyzer takes them. These are the two rules
that keeps: the pass answers ONE table for every unit, and every field of
`SymbolTables` is either a table the pass owns or a field a later step fills.
"""
from __future__ import annotations

import dataclasses

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.collect import CollectorPass
from sushi_lang.semantics.tables import SymbolTables

# The fields the collect pass does NOT fill, and the step that does.
FILLED_LATER = {
    "namespaces": "the namespaces pass",
    "library_not_exported": "the libraries step",
    "pending_extension_instantiations": "the typecheck pass",
    "queued_extension_keys": "the typecheck pass",
    "intern_generic_ref": "the analyzer's late interner",
}

UNIT_A = """
fn alpha() i32:
    return Result.Ok(1)
"""

UNIT_B = """
fn beta() i32:
    return Result.Ok(2)
"""

EVERY_KIND = """
unsafe external "C" as sysio because "the collect pass files an extern too":
    fn text_length(string s) i64 = "strlen"

const i32 LIMIT = 4

struct Crate:
    i32 weight

struct Boxed@(T):
    T held

enum Cursor:
    Start

enum Slot@(T):
    Full(T)

perk Loud:
    fn shout() ~

extend Crate with Loud:
    fn shout() ~:
        return Result.Ok(~)

extend Boxed@(T) with Loud:
    fn shout() ~:
        return Result.Ok(~)

extend Crate heavier() i32:
    return self.weight + 1

extend Boxed@(T) held_twice@(U)(U other) U:
    return other

fn twin@(T)(nom T v) T:
    return Result.Ok(v)

fn main() i32:
    return Result.Ok(0)
"""


def _program(source: str):
    program, _tree = parse_to_ast(source)
    return program


def test_one_table_answers_for_every_unit():
    """Two units, one collector: the tables are filled in place, never copied."""
    collector = CollectorPass(Reporter())

    first = collector.run(_program(UNIT_A), unit_name="a", unit_file="a.sushi")
    second = collector.run(_program(UNIT_B), unit_name="b", unit_file="b.sushi")

    assert first is collector.tables
    assert second is collector.tables
    assert "alpha" in collector.tables.funcs.by_name
    assert "beta" in collector.tables.funcs.by_name


def test_every_program_table_is_the_collect_pass_own_or_filled_later():
    """Totality over `SymbolTables`: no field is left for a merge step to forget."""
    collector = CollectorPass(Reporter())
    tables = collector.tables

    unaccounted = []
    for field in dataclasses.fields(SymbolTables):
        if field.name in FILLED_LATER:
            continue
        owned = getattr(collector, field.name, None)
        if owned is None or getattr(tables, field.name) is not owned:
            unaccounted.append(field.name)

    assert not unaccounted, (
        "these `SymbolTables` fields are neither the collect pass's own table nor "
        f"named in FILLED_LATER: {unaccounted}")


def test_the_collect_pass_fills_every_table_it_owns():
    """One unit that declares every kind: no owned table is left empty."""
    collector = CollectorPass(Reporter())
    tables = collector.run(_program(EVERY_KIND), unit_name="main",
                           unit_file="main.sushi")

    assert "LIMIT" in tables.constants.by_name
    assert "Crate" in tables.structs.by_name
    assert "Boxed" in tables.generic_structs.by_name
    assert "Cursor" in tables.enums.by_name
    assert "Slot" in tables.generic_enums.by_name
    assert "Loud" in tables.perks.by_name
    assert tables.perk_impls.owner("Crate", "Loud") == "main"
    assert tables.generic_perk_impls.templates("Boxed")
    assert "main" in tables.funcs.by_name
    assert "twin" in tables.generic_funcs.by_name
    assert tables.extensions.by_type
    assert tables.generic_extensions.by_type
    assert tables.externals.lookup("sysio", "text_length") is not None
    assert tables.visibility.by_key
