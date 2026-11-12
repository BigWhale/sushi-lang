"""Gate: the typecheck pass has one arm for each SHIR node class (docs/design/ir.md 7.3, 7.9).

The typecheck pass walks a SHIR body through ONE dispatch table, `ARMS` in
`sushi_lang/semantics/passes/types/visit/__init__.py`, keyed on the SHIR class. A pattern
has its own table there, `PATTERN_ARMS` (ruling R17): a pattern arm answers a place in the
matrix of the one checker, and its dispatch writes no type row. The models are `LOWERING`
in `shir/lower_nodes.py` and `PRINTERS` in `shir/printer.py`. So this gate holds four rules:

1. `ARMS` has a row for each member of `ShirStmt` and `ShirExpr`, and `PATTERN_ARMS` a row
   for each member of `ShirPattern`.
2. Neither table has a row for another class.
3. A class with no row is an internal error, never a silent skip.
4. The arm of a construct that the pass does not type is an internal error (CE0000) that
   names the stage and the class, at the span of the node. After an error, it ends the walk
   of the body instead (`dispatch.AfterAnError`), and nothing reports it.

These tests read Python objects and call the dispatch over a node that the test builds.
They do not run the compiler.
"""
from __future__ import annotations

import dataclasses
import typing
from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import dispatch, exprs, patterns, stmts

_UNIONS = {"ShirStmt": stmts.ShirStmt, "ShirExpr": exprs.ShirExpr,
           "ShirPattern": patterns.ShirPattern}
#: The table of each union: a pattern has its own (ruling R17).
_TABLES = {"ShirStmt": visit.ARMS, "ShirExpr": visit.ARMS, "ShirPattern": visit.PATTERN_ARMS}


def _classes() -> set[type]:
    return set().union(*(typing.get_args(union) for union in _UNIONS.values()))


def _names(classes: set[type]) -> list[str]:
    return sorted(f"{cls.__module__}.{cls.__name__}" for cls in classes)


def _node(cls: type, span: Span) -> object:
    """A node of `cls` whose fields hold nothing but its span: no arm reads more here."""
    fields = {each.name: None for each in dataclasses.fields(cls)}
    fields["loc"] = span
    return cls(**fields)


@pytest.mark.parametrize("union", list(_UNIONS), ids=list(_UNIONS))
def test_each_member_of_a_node_union_has_a_row(union: str) -> None:
    missing = set(typing.get_args(_UNIONS[union])) - set(_TABLES[union])
    assert not missing, f"the typecheck pass has no arm for: {_names(missing)}"


def test_the_table_has_no_row_for_another_class() -> None:
    patterns_ = set(typing.get_args(patterns.ShirPattern))
    extra = set(visit.ARMS) - (_classes() - patterns_)
    assert not extra, f"the typecheck pass has an arm for a class of no union: {_names(extra)}"
    extra = set(visit.PATTERN_ARMS) - patterns_
    assert not extra, f"the pattern table has a row for a class that is no pattern: {_names(extra)}"


def test_every_row_is_callable() -> None:
    bad = [cls.__name__ for table in (visit.ARMS, visit.PATTERN_ARMS)
           for cls, arm in table.items() if not callable(arm)]
    assert not bad, f"these rows are no arm: {bad}"


def test_a_class_with_no_row_is_an_internal_error() -> None:
    class NotANode:
        pass

    with pytest.raises(InternalCompilerError) as caught:
        visit.check(object(), NotANode())
    assert caught.value.code == "CE0000"
    assert "NotANode" in str(caught.value.params.get("detail"))


@pytest.mark.parametrize("cls", sorted(_classes(), key=lambda cls: cls.__name__),
                         ids=lambda cls: cls.__name__)
def test_the_unhandled_arm_names_the_stage_the_class_and_the_span(cls: type) -> None:
    span = Span(3, 5, 3, 9)
    with pytest.raises(InternalCompilerError) as caught:
        visit.unhandled(SimpleNamespace(has_an_error=lambda: False), _node(cls, span))
    fault = caught.value
    assert fault.code == "CE0000"
    assert fault.params == {"detail": f"the typecheck stage does not handle the construct "
                                      f"'{cls.__name__}'"}
    assert fault.span == span


def test_the_unhandled_arm_after_an_error_ends_the_walk() -> None:
    with pytest.raises(dispatch.AfterAnError):
        visit.unhandled(SimpleNamespace(has_an_error=lambda: True),
                        _node(stmts.Assign, Span(3, 5, 3, 9)))
