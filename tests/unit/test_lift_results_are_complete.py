"""The completeness check of `LiftResults`, and the rule of one answer per key (docs/design/ir.md 7.9, Q8).

The `lift` pass writes the symbol and the environment layout of each `Lambda` of a body
into its table. When the pass ends on a body with no error, the check walks the body and
finds each `Lambda` with no symbol or no environment. A gap is the internal error CE0157.
These tests give the check the body of `ownership_sample.py` and a table made by hand.
They call a Python function over data; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.lift import Capture, LiftResults
from sushi_lang.semantics.shir.lift_check import check_lift, missing_lift
from sushi_lang.semantics.shir.side_table import MissingAnswer

from ownership_sample import CAPTURED, SYMBOL, lift, sample, span


def test_a_full_table_has_no_gap() -> None:
    made = sample()
    assert missing_lift(made.body, lift(made)) == []
    check_lift(made.body, lift(made), made.decls)


@pytest.mark.parametrize("row", ["symbol", "environment"])
def test_a_missing_row_is_found(row: str) -> None:
    made = sample()
    assert missing_lift(made.body, lift(made, skip=row)) == [MissingAnswer(row, "Lambda", span(15))]


def test_an_empty_table_misses_both_rows() -> None:
    made = sample()
    assert [gap.answer for gap in missing_lift(made.body, LiftResults())] == [
        "symbol", "environment"]


def test_a_gap_is_the_internal_error_ce0157() -> None:
    made = sample()
    with pytest.raises(InternalCompilerError) as caught:
        check_lift(made.body, lift(made, skip="environment"), made.decls)
    error = caught.value
    assert error.code == "CE0157"
    assert error.params == {"writer": "lift", "table": "LiftResults", "answer": "environment",
                            "node": "Lambda", "where": "line 15, column 5", "body": "sample"}
    assert error.span == span(15)


@pytest.mark.parametrize("write", [
    lambda table: table.record_symbol(ShirId(0), "f"),
    lambda table: table.record_environment(ShirId(0), ()),
], ids=["symbol", "environment"])
def test_a_second_answer_for_one_key_is_an_internal_error(write) -> None:
    table = LiftResults()
    write(table)
    with pytest.raises(InternalCompilerError) as caught:
        write(table)
    assert caught.value.code == "CE0000"


def test_a_read_answers_the_row_or_none() -> None:
    made = sample()
    table = lift(made)
    lam = made.id_of("lambda")
    assert table.symbol_of(lam) == SYMBOL
    assert table.environment_of(lam) == (Capture(CAPTURED, moves=False),)
    assert table.symbol_of(ShirId(999)) is None
    assert table.environment_of(ShirId(999)) is None
    assert Capture(LocalId(0), moves=True) != Capture(LocalId(0), moves=False)
