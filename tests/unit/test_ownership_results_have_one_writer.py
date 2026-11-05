"""Gate: the `borrow` pass is the one writer of `OwnershipResults` (docs/design/ir.md 7.9, ruling S6).

`OwnershipResults` is the table of the ownership answers of one body: the `Ownership` of
each consuming use, the conditionally moved locals, LENT or OWNED for an iterator, the heap
decision of a lent iterator, and the walk in place of a loop. Each of those facts is a
question about ownership, and the `borrow` pass sees every input that `typecheck` sees: so
`borrow` writes them, and no other module does (S6). The scan (`side_table_scan.py`) holds
the four rules of the `TypeckResults` gate with the names of this table: only lowering
makes the table, only `semantics/passes/borrow/` names the write API, only
`semantics/shir/ownership.py` names the tables, and the class has a read API and a write
API and nothing else.

This gate takes the place of `test_walk_fields_have_one_writer.py` for SHIR; the AST scan
goes with the two AST fields. The borrow pass on SHIR writes the rows, so the gate also asks
that a module of the pass calls the write API.
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
from sushi_lang.semantics.shir.ownership import OwnershipResults

TABLE = SideTable(
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


def test_the_owner_of_the_bodies_alone_makes_the_table() -> None:
    assert_the_owner_alone_makes(TABLE)


def test_the_borrow_pass_alone_calls_the_write_api() -> None:
    assert_the_writer_alone_writes(TABLE)
    writers = [module for module in calls_of(TABLE.write_api) if module.startswith(TABLE.writer)]
    assert writers, (
        f"no module of {TABLE.writer} calls the write API of OwnershipResults: the borrow "
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
    assert_the_api(TABLE, OwnershipResults)


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_a_copy_of_a_table_is_refused(copier) -> None:
    assert_a_copy_is_refused(OwnershipResults, copier)
