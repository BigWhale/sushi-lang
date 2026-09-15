"""The collect tables hold the two-view rule ONCE (#695).

A name means one thing inside the unit that declared it and another outside, so every
table that answers a bare name carries two views: `by_name` is flat and first-wins, and
`by_unit` keeps each unit's own declaration (`docs/design/unit-namespaces.md` section 9).
Three collect tables wrote that rule out again, byte for byte, beside a
`UnitKeyedSymbols` that already held it.

Two gates: the tables ARE that class and add no copy of its methods, and all three
answer the same rule.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.passes.collect.constants import ConstantTable, ConstSig
from sushi_lang.semantics.passes.collect.functions import (
    FuncSig, FunctionTable, GenericFuncDef, GenericFunctionTable)
from sushi_lang.semantics.unit_symbols import UnitKeyedSymbols

TABLES = (FunctionTable, GenericFunctionTable, ConstantTable)
SHARED = ("declare", "lookup", "view_for")


def _signature(table_type, name: str, unit: str):
    """One declaration of `name`, of the kind `table_type` holds."""
    if table_type is FunctionTable:
        return FuncSig(name=name, unit_name=unit)
    if table_type is ConstantTable:
        return ConstSig(name=name, unit_name=unit)
    return GenericFuncDef(name=name, type_params=(), params=[], ret=None,
                          body=None, unit_name=unit)


@pytest.mark.parametrize("table_type", TABLES, ids=lambda t: t.__name__)
def test_a_collect_table_is_a_unit_keyed_symbol_table(table_type):
    assert issubclass(table_type, UnitKeyedSymbols)
    copies = [name for name in SHARED if name in vars(table_type)]
    assert not copies, (
        f"{table_type.__name__} copies the shared rule: {', '.join(copies)}")
    for name in SHARED:
        assert hasattr(table_type, name), f"{table_type.__name__} lost {name}"


@pytest.mark.parametrize("table_type", TABLES, ids=lambda t: t.__name__)
def test_each_unit_reads_its_own_declaration(table_type):
    table = table_type()
    left = _signature(table_type, "helper", "helpers/left")
    right = _signature(table_type, "helper", "helpers/right")
    table.declare("helper", left)
    table.declare("helper", right)

    assert table.by_name["helper"] is left, "the flat view is first-wins"
    assert table.lookup("helper", "helpers/left") is left
    assert table.lookup("helper", "helpers/right") is right
    assert table.lookup("helper") is left
    assert table.view_for("helpers/right")["helper"] is right
    assert table.view_for(None)["helper"] is left
    assert table.order == ["helper"], "one name is recorded once"


@pytest.mark.parametrize("table_type", TABLES, ids=lambda t: t.__name__)
def test_a_declaration_with_no_unit_stays_flat(table_type):
    table = table_type()
    orphan = _signature(table_type, "loose", None)
    table.declare("loose", orphan)

    assert table.by_name["loose"] is orphan
    assert table.by_unit == {}
    assert table.lookup("loose", "any/unit") is orphan
