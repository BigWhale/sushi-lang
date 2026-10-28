"""The borrow arm of a `match` (docs/design/ir.md 7.9).

A literal arm binds no name: `_`, a literal, a range, and an `Or` of them. A `Variant`
binds the local of each bare `PatBinding` in it, with the span of the variant name that
holds it. A `poke`, `peek` or `nom` binder, a `Tuple` and an `Own` are the patterns of
later groups: a match with one of them stops the walk with `borrow, Match`. The tests run
the arm over SHIR built by hand, with a stand-in for the pass; they do not run the
compiler.
"""
from __future__ import annotations

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.borrow import arms, statements
from sushi_lang.semantics.passes.borrow.bindings import bare_binders
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.ownership import TypeClass
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType

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


@pytest.mark.parametrize("pattern", [
    _variant(s.Ref(BorrowMode.POKE)),
    _variant(s.Ref(BorrowMode.PEEK)),
    _variant(p.Nom()),
    _variant(items=[_variant(p.Nom(), local=2)]),
    p.Tuple(ShirId(41), SPAN, [_lit(1), p.Wildcard(ShirId(42), SPAN)]),
    p.Own(ShirId(43), SPAN, p.PatBinding(LocalId(2), s.Bare())),
    p.Or(ShirId(44), SPAN, [_variant(), _variant(p.Nom())]),
], ids=["poke", "peek", "nom", "nested-nom", "tuple", "own", "or-with-nom"])
def test_a_pattern_of_a_later_group_has_no_bare_binders(pattern: p.ShirPattern) -> None:
    assert bare_binders(pattern) is None


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


class _Types:
    """The stand-in of the type queries: a `string` owns, an `i32` is plain."""

    def type_class(self, ty: object) -> TypeClass:
        return TypeClass.MOVE if ty == BuiltinType.STRING else TypeClass.PLAIN


class _Checker:
    """The stand-in of the pass: the type row of each local, and no state."""
    types = _Types()

    def __init__(self, ty: object) -> None:
        self.ty = ty

    def local_type(self, _local: LocalId) -> object:
        return self.ty


def test_a_bare_binder_of_a_payload_that_owns_stops_with_borrow_match() -> None:
    """The stop comes before the arm reads the scrutinee, so the stand-in has no state."""
    scrutinee = e.LocalRef(ShirId(2), SPAN, LocalId(0))
    node = s.Match(ShirId(1), SPAN, scrutinee,
                   [_arm(_variant()), _arm(p.Wildcard(ShirId(20), SPAN))], False)
    with pytest.raises(StageStop) as caught:
        statements.check_match(_Checker(BuiltinType.STRING), node)  # type: ignore[arg-type]
    assert caught.value.params == {"stage": "borrow", "construct": "Match"}
