"""Gate: each result table of the body passes has ONE writer (docs/design/ir.md 7.3, 7.9).

A result table has one pass that writes it, and no other module does: a table with a second
writer is the stamp channel again (ir.md 1.2). Three tables hold the rule, and each row below
names its data module, its one writer and its two APIs:

- `TypeckResults`: `typecheck` writes it (ruling S5).
- `OwnershipResults`: `borrow` writes it (ruling S6). `borrow` sees every input that
  `typecheck` sees, so the ownership facts are its answers.
- `LiftResults`: `lift` writes it (ruling Q8). `lift` rewrites no SHIR node, so this table
  is all that it gives.

The scan (`side_table_scan.py`) holds four rules for each table: only lowering makes the
table, only the module of the writer names the write API, only the data module names the
tables, and the class has a read API and a write API and nothing else. The writer must call
the write API too: a scan that finds no call in the writer has gone stale. A copy of a table
is refused.
"""
from __future__ import annotations

import copy

import pytest

from side_table_scan import (
    CONSTRUCTION_FORMS, WRITE_FORMS, SideTable, assert_a_copy_is_refused, assert_the_api,
    assert_the_data_module_alone_names_the_tables, assert_the_owner_alone_makes,
    assert_the_scan_finds_a_construction, assert_the_scan_finds_a_write,
    assert_the_scan_lets_a_reader_and_the_writer_pass, assert_the_writer_alone_writes, calls_of,
)
from sushi_lang.semantics.shir.lift import LiftResults
from sushi_lang.semantics.shir.ownership import OwnershipResults
from sushi_lang.semantics.shir.typeck import TypeckResults

TYPECK = SideTable(
    name="TypeckResults",
    data_module="semantics/shir/typeck.py",
    writer="semantics/passes/types/",
    read_api=frozenset({
        "type_of", "local_type", "capture_type", "callee_of", "convention_of", "instance_of",
        "field_index_of", "loop_kind_of", "range_value_of", "conversion_of", "try_facts_of",
        "display_type_of", "match_facts_of", "element_type_of", "value_of", "global_of", "global_type",
        "iterator_next_of", "sources_of",
    }),
    write_api=frozenset({
        "record_type", "record_local", "record_capture", "record_callee", "record_convention",
        "record_instance", "record_field_index", "record_loop_kind", "record_range_value",
        "record_conversion", "record_try", "record_display", "record_match", "record_element",
        "record_value", "record_global", "record_iterator_next", "record_sources",
    }),
    tables=frozenset({
        "_node_types", "_local_types", "_capture_types", "_callees", "_arg_conventions",
        "_instance_types", "_field_indices", "_loop_kinds", "_range_values", "_conversions",
        "_try_facts", "_display_types", "_match_facts", "_element_types", "_values", "_globals",
        "_source_rows",
    }),
)

OWNERSHIP = SideTable(
    name="OwnershipResults",
    data_module="semantics/shir/ownership.py",
    writer="semantics/passes/borrow/",
    read_api=frozenset({
        "ownership_of", "capture_ownership_of", "is_conditionally_moved",
        "conditionally_moved", "lent_of", "lent_on_heap", "in_place_of", "region_of",
        "regions",
    }),
    write_api=frozenset({
        "record_ownership", "record_capture_ownership", "record_conditional_move",
        "record_lent", "record_lent_on_heap", "record_in_place", "record_region",
    }),
    tables=frozenset({
        "_ownerships", "_capture_ownerships", "_conditional_moves", "_lent_iterators",
        "_lent_on_heap", "_in_place_walks", "_regions",
    }),
)

LIFT = SideTable(
    name="LiftResults",
    data_module="semantics/shir/lift.py",
    writer="semantics/passes/lift.py",
    read_api=frozenset({"symbol_of", "environment_of"}),
    write_api=frozenset({"record_symbol", "record_environment"}),
    tables=frozenset({"_symbols", "_environments"}),
)

#: Each table with its class.
TABLES = {
    "typeck": (TYPECK, TypeckResults),
    "ownership": (OWNERSHIP, OwnershipResults),
    "lift": (LIFT, LiftResults),
}
_BY_TABLE = pytest.mark.parametrize("table", [row[0] for row in TABLES.values()],
                                    ids=list(TABLES))


@_BY_TABLE
def test_the_owner_of_the_bodies_alone_makes_the_table(table: SideTable) -> None:
    assert_the_owner_alone_makes(table)


@_BY_TABLE
def test_the_writer_alone_calls_the_write_api(table: SideTable) -> None:
    assert_the_writer_alone_writes(table)
    writers = [module for module in calls_of(table.write_api) if module.startswith(table.writer)]
    assert writers, (
        f"no module of {table.writer} calls the write API of {table.name}: the pass "
        "writes no answer, or the scan has gone stale")


@_BY_TABLE
@pytest.mark.parametrize("form", WRITE_FORMS)
def test_the_scan_finds_each_form_of_a_write(table: SideTable, form: str) -> None:
    assert_the_scan_finds_a_write(table, form)


@_BY_TABLE
@pytest.mark.parametrize("form", CONSTRUCTION_FORMS)
def test_the_scan_finds_each_form_of_a_construction(table: SideTable, form: str) -> None:
    assert_the_scan_finds_a_construction(table, form)


@_BY_TABLE
def test_the_scan_lets_a_reader_and_the_writer_pass(table: SideTable) -> None:
    assert_the_scan_lets_a_reader_and_the_writer_pass(table)


@_BY_TABLE
def test_the_data_module_alone_names_the_tables(table: SideTable) -> None:
    assert_the_data_module_alone_names_the_tables(table)


@pytest.mark.parametrize("table", list(TABLES), ids=list(TABLES))
def test_the_table_has_a_read_api_and_a_write_api_and_nothing_else(table: str) -> None:
    row, cls = TABLES[table]
    assert_the_api(row, cls)


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
@pytest.mark.parametrize("table", list(TABLES), ids=list(TABLES))
def test_a_copy_of_a_table_is_refused(table: str, copier) -> None:
    assert_a_copy_is_refused(TABLES[table][1], copier)
