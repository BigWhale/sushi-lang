"""Gate: the readable fold answers for each SHIR expression class (docs/design/ir.md 7.4).

The fold of the typecheck pass (`semantics/passes/types/fold.py`) has ONE table, `FOLDS`,
with an arm for each class that can be readable, and ONE set, `UNREADABLE`, of the
classes that make a sub-expression unreadable (ruling R-E). This gate holds three rules:

1. Each member of `ShirExpr` is in `FOLDS` or in `UNREADABLE`, and in one of them only.
2. No other class is in either.
3. A node of an unreadable class has no value, and the fold calls no arm for it.

These tests read Python objects and call the fold over nodes that they build. They do not
run the compiler.
"""
from __future__ import annotations

import dataclasses
import typing

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import fold
from sushi_lang.semantics.shir import exprs

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
