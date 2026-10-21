"""Gate: the `lift` pass is the one writer of `LiftResults` (docs/design/ir.md 7.9, ruling Q8).

`LiftResults` is the table of the lifted lambdas of one body: the symbol of each lifted
function and its environment layout, by the `shir_id` of the `Lambda`. `lift` rewrites no
SHIR node (Q8), so this table is all that it gives, and no other module writes it. The
scan (`side_table_scan.py`) holds the four rules of the `TypeckResults` gate with the
names of this table: only lowering makes the table, only `semantics/passes/lift.py` names
the write API, only `semantics/shir/lift.py` names the tables, and the class has a read API
and a write API and nothing else.

The lift pass on SHIR writes no row yet (P2.7), so the gate does not ask that the pass
calls the write API: P2.7 adds that check.
"""
from __future__ import annotations

import copy

import pytest

from side_table_scan import (
    CONSTRUCTION_FORMS, WRITE_FORMS, SideTable, assert_a_copy_is_refused, assert_the_api,
    assert_the_data_module_alone_names_the_tables, assert_the_owner_alone_makes,
    assert_the_scan_finds_a_construction, assert_the_scan_finds_a_write,
    assert_the_scan_lets_a_reader_and_the_writer_pass, assert_the_writer_alone_writes,
)
from sushi_lang.semantics.shir.lift import LiftResults

TABLE = SideTable(
    name="LiftResults",
    data_module="semantics/shir/lift.py",
    writer="semantics/passes/lift.py",
    read_api=frozenset({"symbol_of", "environment_of"}),
    write_api=frozenset({"record_symbol", "record_environment"}),
    tables=frozenset({"_symbols", "_environments"}),
)


def test_the_owner_of_the_bodies_alone_makes_the_table() -> None:
    assert_the_owner_alone_makes(TABLE)


def test_the_lift_pass_alone_calls_the_write_api() -> None:
    assert_the_writer_alone_writes(TABLE)


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
    assert_the_api(TABLE, LiftResults)


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
def test_a_copy_of_a_table_is_refused(copier) -> None:
    assert_a_copy_is_refused(LiftResults, copier)
