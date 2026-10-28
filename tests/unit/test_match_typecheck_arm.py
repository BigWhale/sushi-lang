"""The typecheck arm of a `match` on an integer or a string (docs/design/ir.md 7.3, 7.8).

A match on an integer or a string records its `MatchFacts`: the type of the scrutinee, and
the integer type that a literal arm and a range arm are read at (None on a string). Then it
checks each arm, walks its body, and runs the one checker over the rows of the arms; the
walk goes on after the match. A match on an enum or a tuple stops with `typecheck, Match`.
The value of an integer pattern is read at the type of its position
(`semantics/integer_patterns.py`). A pattern has its own table of arms (ruling R17): its
dispatch answers the place of the pattern in the matrix and writes no type row, and the
expression table has no pattern row. The tests run the arm over SHIR built by hand, with a
stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import typing

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError, StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.integer_patterns import goes_down, literal_value, pattern_interval
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.passes.types.exhaustiveness import Ctor, IntRange
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.typeck import MatchFacts, TypeckResults
from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo

SPAN = Span(4, 5, 4, 20)
I8, U8, I32 = BuiltinType.I8, BuiltinType.U8, BuiltinType.I32
STRING = BuiltinType.STRING
COLOR = EnumType("Color", (EnumVariantInfo("Red", ()), EnumVariantInfo("Green", ())))


def _validator(answers: TypeckResults) -> SimpleNamespace:
    empty = SimpleNamespace(by_name={})
    return SimpleNamespace(body_answers=lambda: answers, struct_table=empty,
                           enum_table=empty, visibility=None, reporter=None,
                           uncovered_matches=set())


def _arm(index: int, pattern: p.ShirPattern) -> p.Arm:
    return p.Arm(pattern, ShirBlock(ShirId(100 + index), SPAN, []))


def _int(value: int, radix: int = 10) -> e.IntConst:
    return e.IntConst(value, radix)


def _written(const: e.Constant) -> str:
    """The source text of a constant of these tests: a decimal, or a lower-case hex."""
    if isinstance(const, e.StrConst):
        return f'"{const.value}"'
    return hex(const.value) if const.radix == 16 else str(const.value)


def _lit(ident: int, const: e.Constant) -> p.Literal:
    return p.Literal(ShirId(ident), SPAN, const, _written(const))


def _range(low: e.IntConst, high: e.IntConst, inclusive: bool) -> p.IntRange:
    return p.IntRange(ShirId(50), SPAN, p.RangeBound(low, _written(low), SPAN),
                      p.RangeBound(high, _written(high), SPAN), inclusive)


def _match(ty, arms: list[p.Arm]) -> tuple[s.Match, TypeckResults, SimpleNamespace]:
    answers = TypeckResults()
    answers.record_local(LocalId(0), ty)
    node = s.Match(ShirId(1), SPAN, e.LocalRef(ShirId(2), SPAN, LocalId(0)), arms, False)
    return node, answers, _validator(answers)


def _stop(validator: SimpleNamespace, node: s.Match) -> dict:
    with pytest.raises(StageStop) as caught:
        visit.check(validator, node)
    assert caught.value.span == SPAN
    return caught.value.params


def test_an_integer_match_records_its_facts_and_is_covered() -> None:
    node, answers, validator = _match(I32, [
        _arm(0, _lit(10, _int(1))),
        _arm(1, p.Or(ShirId(11), SPAN, [_lit(12, _int(2)),
                                        _lit(13, _int(0x7f, 16))])),
        _arm(2, _range(_int(0x10, 16), _int(0x1f, 16), True)),
        _arm(3, p.Wildcard(ShirId(14), SPAN)),
    ])
    visit.check(validator, node)
    assert validator.uncovered_matches == set()
    assert answers.match_facts_of(node.shir_id) == MatchFacts(I32, I32)
    assert answers.type_of(node.scrutinee.shir_id) == I32


def test_a_string_match_has_no_integer_type() -> None:
    node, answers, validator = _match(STRING, [
        _arm(0, _lit(10, e.StrConst("get"))),
        _arm(1, p.Wildcard(ShirId(11), SPAN)),
    ])
    visit.check(validator, node)
    assert validator.uncovered_matches == set()
    assert answers.match_facts_of(node.shir_id) == MatchFacts(STRING, None)


def test_an_enum_match_stops_with_no_facts() -> None:
    node, answers, validator = _match(COLOR, [_arm(0, p.Wildcard(ShirId(10), SPAN))])
    assert _stop(validator, node) == {"stage": "typecheck", "construct": "Match"}
    assert answers.match_facts_of(node.shir_id) is None


@pytest.mark.parametrize("const, ty, value", [
    (_int(0xff, 16), I8, -1),
    (_int(0xff, 16), U8, 255),
    (_int(-1), I8, -1),
    (_int(200), U8, 200),
])
def test_a_literal_is_read_at_the_type_of_its_position(const, ty, value) -> None:
    assert literal_value(const, ty) == value
    assert pattern_interval(_lit(1, const), ty) == (value, value)


@pytest.mark.parametrize("low, high, inclusive, ty, interval", [
    (_int(0x80, 16), _int(0xff, 16), True, I8, (-128, -1)),
    (_int(0), _int(10), False, I32, (0, 9)),
    (_int(5), _int(5), False, I32, (5, 4)),
])
def test_a_range_is_read_at_the_type_of_its_position(low, high, inclusive, ty,
                                                     interval) -> None:
    node = _range(low, high, inclusive)
    assert pattern_interval(node, ty) == interval
    assert not goes_down(node, ty)


def test_a_range_whose_start_is_above_its_end_goes_down() -> None:
    assert goes_down(_range(_int(0x8f, 16), _int(0x80, 16), True), U8)


def test_the_expression_table_has_no_pattern_row() -> None:
    assert not set(typing.get_args(p.ShirPattern)) & set(visit.ARMS)
    answers = TypeckResults()
    literal = _lit(10, _int(1))
    with pytest.raises(InternalCompilerError) as caught:
        visit.check_expr(_validator(answers), literal, I32)
    assert not isinstance(caught.value, StageStop)
    assert caught.value.code == "CE0000"
    assert answers.type_of(literal.shir_id) is None


def test_the_pattern_dispatch_answers_a_row_and_writes_no_type() -> None:
    answers = TypeckResults()
    validator = _validator(answers)
    literal = _lit(10, _int(0xff, 16))
    assert visit.check_pattern(validator, literal, I8) == Ctor(IntRange(-1, -1))
    bounds = _range(_int(0x10, 16), _int(0x1f, 16), True)
    assert visit.check_pattern(validator, bounds, U8) == Ctor(IntRange(0x10, 0x1f))
    assert answers.type_of(literal.shir_id) is None
    assert answers.type_of(bounds.shir_id) is None
