"""The borrow arm of a `match` (docs/design/ir.md 7.9).

A literal arm binds no name: `_`, a literal, a range, and an `Or` of them. A `Variant`
and a `Tuple` bind the local of each bare `PatBinding` in them, with the span of the
variant name or of the tuple pattern that holds it. A `nom` binder is no bare binder: the
arm takes its payload (`pattern_takes.taken_binders`). A `poke` or `peek` binder of a
variant payload or of a tuple part is the pattern of a later group: a match with one stops
the walk with `borrow, Match`. The tests run
the arm over SHIR built by hand, with a stand-in for the pass; they do not run the
compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, statements
from sushi_lang.semantics.passes.borrow.bindings import bare_binders
from sushi_lang.semantics.passes.borrow.pattern_takes import taken_binders
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.typesys import BorrowMode

SPAN = Span(4, 5, 4, 20)
NAME = Span(4, 11, 4, 15)
INNER = Span(4, 16, 4, 22)
BARE = s.Bare()


def _lit(value: int) -> p.Literal:
    return p.Literal(ShirId(10 + value), SPAN, e.IntConst(value, 10), str(value))


def _range() -> p.IntRange:
    return p.IntRange(ShirId(30), SPAN, p.RangeBound(e.IntConst(1, 10), "1", SPAN),
                      p.RangeBound(e.IntConst(9, 10), "9", SPAN), True)


def _variant(by: p.BindMode = BARE, local: int = 1, at: Span = NAME,
             items: list | None = None) -> p.Variant:
    binding = p.PatBinding(LocalId(local), by)
    return p.Variant(ShirId(40 + local), SPAN, DeclNames().refer("Maybe"), "Some",
                     [binding] if items is None else items, SPAN, at)


def _arm(pattern: p.ShirPattern) -> p.Arm:
    return p.Arm(pattern, ShirBlock(ShirId(90), SPAN, []))


@pytest.mark.parametrize("pattern", [
    p.Wildcard(ShirId(20), SPAN),
    _lit(1),
    _range(),
    p.Or(ShirId(31), SPAN, [_lit(1), _lit(2), _range()]),
], ids=["wildcard", "literal", "range", "or"])
def test_a_literal_arm_binds_no_name(pattern: p.ShirPattern) -> None:
    assert bare_binders(pattern) == []


def test_a_variant_binds_each_bare_binder_at_the_name_of_its_variant() -> None:
    nested = _variant(local=2, at=INNER)
    outer = _variant(at=NAME, items=[p.PatBinding(LocalId(1), s.Bare()), _lit(3), nested,
                                     p.Wildcard(ShirId(21), SPAN)])
    assert bare_binders(outer) == [(LocalId(1), NAME), (LocalId(2), INNER)]


def test_the_alternatives_of_or_share_one_binder_with_the_span_of_the_first() -> None:
    first = _variant(at=NAME)
    second = _variant(at=INNER)
    assert bare_binders(p.Or(ShirId(44), SPAN, [first, second])) == [(LocalId(1), NAME)]


TUPLE = Span(4, 9, 4, 18)


def test_a_tuple_binds_each_bare_part_at_the_tuple_pattern() -> None:
    """A part binder takes the span of its tuple pattern, a variant in a part the span of its
    variant name, and a tuple in a payload the span of the tuple, as on `main`."""
    nested = p.Tuple(ShirId(45), INNER, [p.PatBinding(LocalId(3), s.Bare()), _lit(1)])
    tuple_ = p.Tuple(ShirId(46), TUPLE, [p.PatBinding(LocalId(1), s.Bare()),
                                         _variant(local=2, at=NAME),
                                         _variant(local=4, at=NAME, items=[nested]),
                                         p.Wildcard(ShirId(47), SPAN)])
    assert bare_binders(tuple_) == [(LocalId(1), TUPLE), (LocalId(2), NAME),
                                    (LocalId(3), INNER)]


@pytest.mark.parametrize("pattern", [
    _variant(s.Ref(BorrowMode.POKE)),
    _variant(s.Ref(BorrowMode.PEEK)),
    p.Own(ShirId(43), SPAN, p.PatBinding(LocalId(2), s.Bare())),
], ids=["poke", "peek", "own"])
def test_a_pattern_of_a_later_group_has_no_bare_binders(pattern: p.ShirPattern) -> None:
    assert bare_binders(pattern) is None


@pytest.mark.parametrize("pattern, taken", [
    (_variant(p.Nom()), [LocalId(1)]),
    (_variant(items=[_variant(p.Nom(), local=2)]), [LocalId(2)]),
    (p.Tuple(ShirId(41), SPAN, [_lit(1), p.PatBinding(LocalId(2), p.Nom())]), [LocalId(2)]),
    (p.Or(ShirId(44), SPAN, [_variant(p.Nom()), _variant(p.Nom())]), [LocalId(1)]),
], ids=["nom", "nested-nom", "tuple-with-nom", "or-with-nom"])
def test_a_nom_binder_is_taken_and_is_no_bare_binder(pattern: p.ShirPattern,
                                                     taken: list[LocalId]) -> None:
    assert bare_binders(pattern) == []
    assert taken_binders(pattern) == taken


def test_the_table_checks_a_match() -> None:
    assert arms.ARMS[s.Match] is statements.check_match


def test_a_match_with_a_binder_of_a_later_group_stops_with_borrow_match() -> None:
    """The stop comes before the arm reads the pass, so the stand-in is never read."""
    scrutinee = e.LocalRef(ShirId(2), SPAN, LocalId(0))
    node = s.Match(ShirId(1), SPAN, scrutinee,
                   [_arm(_variant(s.Ref(BorrowMode.POKE))),
                    _arm(p.Wildcard(ShirId(20), SPAN))], False)
    with pytest.raises(StageStop) as caught:
        statements.check_match(object(), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": "Match"}
    assert caught.value.span == SPAN
