"""The AST builder is the third caller of the constant evaluator, and it folds once.

A fixed array's size may name an integer constant of the same unit, and the size is
read while the unit is PARSED (Known Limitation 12) -- long before the collect pass has
a constant table. So the builder keeps its own. It used to build that table from
scratch on EVERY size it met: a unit with C constants and A sizes paid A x C inserts, A
evaluator constructions, and A fresh memos, none of which the other two callers share.

One table per unit, and one answer per name: the builder asks the evaluator once for
each constant a size names, however many sizes name it.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.parser import parse_to_ast
from sushi_lang.semantics.typesys import ArrayType

THREE_CONSTANTS_FOUR_SIZES = """
const i32 SMALL = 2
const i32 MEDIUM = 4
const i32 LARGE = MEDIUM * 2

struct Rows:
    i32[SMALL] a
    i32[SMALL] b
    i32[LARGE] c
    i32[LARGE] d

fn main() i32:
    return Result.Ok(0)
"""

CHAINED = """
const i32 HALF = 2
const i32 FULL = HALF * 2

struct Slots:
    i32[FULL] room

fn main() i32:
    return Result.Ok(0)
"""


@pytest.fixture
def counted(monkeypatch):
    """Count what the builder builds while one unit is parsed."""
    from sushi_lang.semantics import const_eval
    from sushi_lang.semantics.passes.collect import constants as collect_constants

    tally = {"tables": 0, "evaluators": 0}
    real_table = collect_constants.ConstantTable
    real_evaluator = const_eval.ConstantEvaluator

    class CountedTable(real_table):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            tally["tables"] += 1
            super().__init__(*args, **kwargs)

    class CountedEvaluator(real_evaluator):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            tally["evaluators"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(collect_constants, "ConstantTable", CountedTable)
    monkeypatch.setattr(const_eval, "ConstantEvaluator", CountedEvaluator)
    return tally


def test_one_constant_table_serves_every_size_of_a_unit(counted):
    """Four sizes over three constants: the table is built once, not four times."""
    parse_to_ast(THREE_CONSTANTS_FOUR_SIZES)

    assert counted["tables"] == 1


def test_a_repeated_size_is_answered_from_the_first_fold(counted):
    """Four sizes naming two constants: two folds, one per NAME."""
    parse_to_ast(THREE_CONSTANTS_FOUR_SIZES)

    assert counted["evaluators"] == 2


def test_the_sizes_are_still_read_correctly():
    """The counts a unit spells, chained constants included."""
    program, _tree = parse_to_ast(THREE_CONSTANTS_FOUR_SIZES)
    rows = program.structs[0]
    assert [field.ty.size for field in rows.fields] == [2, 2, 8, 8]
    for field in rows.fields:
        assert isinstance(field.ty, ArrayType)

    chained, _tree = parse_to_ast(CHAINED)
    assert chained.structs[0].fields[0].ty.size == 4
