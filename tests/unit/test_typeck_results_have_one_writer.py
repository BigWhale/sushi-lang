"""Gate: the `typecheck` pass is the one writer of `TypeckResults` (docs/design/ir.md 7.3, ruling S5).

`TypeckResults` is the table of one body: every answer that `typecheck` gives, keyed by a
node, a local or a position. Lowering lists each body with its table, the pass writes the
rows, and `lift`, `borrow`, SLIR lowering and the dump read them. A table with a second
writer is the stamp channel again (ir.md 1.2). The scan (`side_table_scan.py`, shared with
the gates of the other side tables) holds four rules: only lowering makes the table, only
the modules of the typecheck pass (`semantics/passes/types/`) name the write API, only
`semantics/shir/typeck.py` names the tables, and the class has a read API and a write API
and nothing else.

The pass must call the write API too: a scan that finds no call in the typecheck pass has
gone stale.
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
from sushi_lang.semantics.shir.typeck import TypeckResults

TABLE = SideTable(
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


def test_the_owner_of_the_bodies_alone_makes_the_table() -> None:
    assert_the_owner_alone_makes(TABLE)


def test_the_typecheck_pass_alone_calls_the_write_api() -> None:
    assert_the_writer_alone_writes(TABLE)
    writers = [module for module in calls_of(TABLE.write_api) if module.startswith(TABLE.writer)]
    assert writers, (
        f"no module of {TABLE.writer} calls the write API of TypeckResults: the typecheck "
        "pass writes no answer, or the scan has gone stale")


@pytest.mark.parametrize("form", WRITE_FORMS)
def test_the_scan_finds_each_form_of_a_write(form: str) -> None:
    assert_the_scan_finds_a_write(TABLE, form)


@pytest.mark.parametrize("form", CONSTRUCTION_FORMS)
def test_the_scan_finds_each_form_of_a_construction(form: str) -> None:
    assert_the_scan_finds_a_construction(TABLE, form)


def test_the_scan_lets_a_reader_and_the_writer_pass() -> None:
    assert_the_scan_lets_a_reader_and_the_writer_pass(TABLE)


def test_the_data_module_alone_names_the_tables() -> None:
    assert_the_data_module_alone_names_the_tables(TABLE)


def test_the_table_has_a_read_api_and_a_write_api_and_nothing_else() -> None:
    assert_the_api(TABLE, TypeckResults)


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_a_copy_of_a_table_is_refused(copier) -> None:
    assert_a_copy_is_refused(TypeckResults, copier)
