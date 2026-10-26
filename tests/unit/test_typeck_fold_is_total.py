"""Gate: the readable fold answers for each SHIR expression class (docs/design/ir.md 7.4).

The fold of the typecheck pass (`semantics/passes/types/fold.py`) has ONE table, `FOLDS`,
with an arm for each class that can be readable, and ONE set, `UNREADABLE`, of the
classes that make a sub-expression unreadable (ruling R-E). This gate holds five rules:

1. Each member of `ShirExpr` is in `FOLDS` or in `UNREADABLE`, and in one of them only.
2. No other class is in either.
3. A node of an unreadable class has no value, and the fold calls no arm for it.
4. The float table of the constant evaluator holds each overflow-checked operator, because
   the fold sends such an operator on a float to that table.
5. An integer literal under a cast to an integer type is read at the target, and the cast
   keeps the bits of the target, as the arm of the cast types it.

These tests read Python objects and call the fold over nodes that they build. They do not
run the compiler.
"""
from __future__ import annotations

import dataclasses
import typing

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics import const_eval, integer_ops
from sushi_lang.semantics.passes.types import fold
from sushi_lang.semantics.shir import exprs
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.typesys import BuiltinType

_MEMBERS = set(typing.get_args(exprs.ShirExpr))


def _names(classes: set[type]) -> list[str]:
    return sorted(cls.__name__ for cls in classes)


def test_each_expression_class_has_a_fold_arm_or_is_unreadable() -> None:
    missing = _MEMBERS - set(fold.FOLDS) - fold.UNREADABLE
    assert not missing, f"the fold has no answer for: {_names(missing)}"


def test_no_class_is_both_readable_and_unreadable() -> None:
    both = set(fold.FOLDS) & fold.UNREADABLE
    assert not both, f"these classes have an arm and are unreadable: {_names(both)}"


def test_the_fold_names_no_other_class() -> None:
    extra = (set(fold.FOLDS) | fold.UNREADABLE) - _MEMBERS
    assert not extra, f"the fold names a class of no expression union: {_names(extra)}"


@pytest.mark.parametrize("cls", sorted(fold.UNREADABLE, key=lambda cls: cls.__name__),
                         ids=lambda cls: cls.__name__)
def test_a_node_of_an_unreadable_class_has_no_value(cls: type) -> None:
    fields = {each.name: None for each in dataclasses.fields(cls)}
    fields["loc"] = Span(1, 1, 1, 2)
    node = cls(**fields)
    assert fold.Folder(lambda _decl: None).read(node, None) is None


def test_the_float_table_holds_each_overflow_checked_operator() -> None:
    assert set(const_eval._FLOAT_ARITHMETIC) == integer_ops.OVERFLOW_CHECKED


_SPAN = Span(1, 1, 1, 2)


def _lit(value: int) -> exprs.Lit:
    return exprs.Lit(ShirId(1), _SPAN, exprs.IntConst(value))


@pytest.mark.parametrize("operand,target,value", [
    (_lit(300), BuiltinType.U8, 44),
    (exprs.Unary(ShirId(2), _SPAN, "neg", _lit(1)), BuiltinType.U64, (1 << 64) - 1),
    (_lit(5000000000), BuiltinType.U32, 705032704),
    (_lit(40000), BuiltinType.I16, -25536),
], ids=["300 as u8", "-1 as u64", "5000000000 as u32", "40000 as i16"])
def test_a_literal_under_a_cast_keeps_the_bits_of_the_target(
        operand: exprs.ShirExpr, target: BuiltinType, value: int) -> None:
    cast = exprs.Cast(ShirId(0), _SPAN, operand, target)
    folded = fold.Folder(lambda _decl: None).scalar(cast, None)
    assert folded is not None and (folded.value, folded.semantic_type) == (value, target)
