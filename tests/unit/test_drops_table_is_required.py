"""A drops provider with no table is a construction error, not an empty answer (#780).

`owns_resource` takes `drops` with no default, because an empty set answers False for
every handle. The providers of that set must not make one up when their table is missing.
"""
import pytest

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.passes.borrow import BorrowChecker
from sushi_lang.semantics.passes.borrow.types import TypeQueries
from sushi_lang.semantics.tables import SymbolTables






def test_borrow_checker_refuses_missing_tables():
    with pytest.raises(TypeError):
        BorrowChecker(Reporter(source="", filename="probe.sushi"))


def test_type_queries_refuse_missing_tables():
    with pytest.raises(TypeError):
        TypeQueries()


def test_borrow_drops_read_the_perk_table():
    tables = SymbolTables()
    tables.perk_impls.by_perk["Drop"] = {"Handle"}
    assert TypeQueries(tables).drops == frozenset({"Handle"})
