"""The borrow arm of a `match` on an integer or a string (docs/design/ir.md 7.9).

A literal arm binds no name: `_`, a literal, a range, and an `Or` of them. The borrow pass
checks a match whose arms are all literal arms. A pattern that binds a name is the
pattern of an enum or a tuple match, and the group of the enum match checks it: until
then, the arm stops the walk with `borrow, Match`. The tests run the arm over SHIR built
by hand, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, statements
from sushi_lang.semantics.passes.borrow.bindings import is_literal_arm
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock

SPAN = Span(4, 5, 4, 20)


def _lit(value: int) -> p.Literal:
    return p.Literal(ShirId(10 + value), SPAN, e.IntConst(value, 10), str(value))


def _range() -> p.IntRange:
    return p.IntRange(ShirId(30), SPAN, p.RangeBound(e.IntConst(1, 10), "1", SPAN),
                      p.RangeBound(e.IntConst(9, 10), "9", SPAN), True)


def _variant() -> p.Variant:
    binding = p.PatBinding(LocalId(1), s.Bare())
    return p.Variant(ShirId(40), SPAN, DeclNames().refer("Maybe"), "Some", [binding])


def _arm(pattern: p.ShirPattern) -> p.Arm:
    return p.Arm(pattern, ShirBlock(ShirId(90), SPAN, []))


@pytest.mark.parametrize("pattern", [
    p.Wildcard(ShirId(20), SPAN),
    _lit(1),
    _range(),
    p.Or(ShirId(31), SPAN, [_lit(1), _lit(2), _range()]),
], ids=["wildcard", "literal", "range", "or"])
def test_a_literal_arm_binds_no_name(pattern: p.ShirPattern) -> None:
    assert is_literal_arm(pattern)


@pytest.mark.parametrize("pattern", [
    _variant(),
    p.Tuple(ShirId(41), SPAN, [_lit(1), p.Wildcard(ShirId(42), SPAN)]),
    p.Own(ShirId(43), SPAN, p.PatBinding(LocalId(2), s.Bare())),
    p.Or(ShirId(44), SPAN, [_lit(1), _variant()]),
], ids=["variant", "tuple", "own", "or-of-variant"])
def test_a_pattern_of_an_enum_or_a_tuple_is_no_literal_arm(pattern: p.ShirPattern) -> None:
    assert not is_literal_arm(pattern)


def test_the_table_checks_a_match() -> None:
    assert arms.ARMS[s.Match] is statements.check_match


def test_a_match_with_a_binding_arm_stops_with_borrow_match() -> None:
    """The stop comes before the arm reads the pass, so the stand-in is never read."""
    scrutinee = e.LocalRef(ShirId(2), SPAN, LocalId(0))
    node = s.Match(ShirId(1), SPAN, scrutinee,
                   [_arm(_variant()), _arm(p.Wildcard(ShirId(20), SPAN))], False)
    with pytest.raises(StageStop) as caught:
        statements.check_match(object(), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": "Match"}
    assert caught.value.span == SPAN
