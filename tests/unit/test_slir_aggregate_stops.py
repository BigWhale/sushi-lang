"""SLIR lowering stops at a construction, a field, a write and a match of a value that owns (docs/design/ir.md 8.9).

A struct or a tuple whose type owns, a field of a temporary that owns, a write of a field
that owns, and a match on a tuple that owns need a drop or a move that Tier B builds. The
tests call the arms of `lower_aggregate.py` and `lower_match.py` over data, before the arm
evaluates a part; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import LocalId, ShirId
from sushi_lang.semantics.shir.typeck import MatchFacts, TypeckResults
from sushi_lang.semantics.slir import lower_aggregate as agg
from sushi_lang.semantics.slir import lower_match
from sushi_lang.semantics.typesys import BorrowMode, BuiltinType, ReferenceType, StructType, Type

SPAN = Span(4, 5, 4, 30)
STRING, I32 = BuiltinType.STRING, BuiltinType.I32
NAMED = StructType(name="Named", fields=(("name", STRING), ("n", I32)))


def _low(answers: TypeckResults, owning: tuple[Type, ...] = (STRING, NAMED)) -> Any:
    """The parts of a `Lowering` that an arm reads before it evaluates a part."""
    return SimpleNamespace(typeck=answers, owns=lambda ty: ty in owning,
                           type_of=lambda node: answers.type_of(node.shir_id))


def _stops(arm: Callable[..., object], construct: str, *args: object) -> None:
    with pytest.raises(StageStop) as caught:
        arm(*args)
    assert caught.value.params == {"stage": "slir", "construct": construct}
    assert caught.value.span == SPAN


def _typed(answers: TypeckResults, node: e.ShirExpr, ty: Type) -> e.ShirExpr:
    answers.record_type(node.shir_id, ty)
    return node


def test_a_struct_whose_type_owns_stops() -> None:
    answers = TypeckResults()
    name = _typed(answers, e.Lit(ShirId(2), SPAN, e.StrConst("towel")), STRING)
    count = _typed(answers, e.Lit(ShirId(3), SPAN, e.IntConst(42)), I32)
    node = e.StructInit(ShirId(1), SPAN, DeclNames().refer("Named"),
                        [e.Arg(name, None), e.Arg(count, None)], (0, 1), None, False, SPAN)
    answers.record_instance(node.shir_id, NAMED)
    _stops(agg.struct_init, "StructInit owning", _low(answers), node)


def test_a_struct_with_a_field_of_a_reference_type_stops() -> None:
    """CE2415 refuses the declaration; the body of a construction can still reach SLIR."""
    answers = TypeckResults()
    value = _typed(answers, e.LocalRef(ShirId(2), SPAN, LocalId(1)), I32)
    node = e.StructInit(ShirId(1), SPAN, DeclNames().refer("Holder"), [e.Arg(value, None)],
                        (0,), None, False, SPAN)
    answers.record_instance(node.shir_id, StructType(
        name="Holder", fields=(("value", ReferenceType(I32, BorrowMode.PEEK)),)))
    _stops(agg.struct_init, "StructInit reference field", _low(answers), node)


def test_a_tuple_whose_type_owns_stops() -> None:
    answers = TypeckResults()
    part = _typed(answers, e.Lit(ShirId(2), SPAN, e.StrConst("x")), STRING)
    node = e.TupleInit(ShirId(1), SPAN, [part])
    pair = StructType(name="$Tuple<string>", fields=(("0", STRING),))
    answers.record_instance(node.shir_id, pair)
    _stops(agg.tuple_init, "TupleInit owning", _low(answers, (pair,)), node)


def test_a_field_of_a_temporary_that_owns_stops() -> None:
    answers = TypeckResults()
    callee = e.ConstRef(ShirId(3), SPAN, DeclNames().refer("make"))
    call = _typed(answers, e.Call(ShirId(2), SPAN, e.Named(callee), []), NAMED)
    field = _typed(answers, e.Field(ShirId(1), SPAN, call, "n", SPAN), I32)
    answers.record_field_index(field.shir_id, 1)
    _stops(agg.place_of, "owning temporary", _low(answers), field)


def test_a_write_of_a_field_that_owns_stops() -> None:
    answers = TypeckResults()
    base = e.LocalRef(ShirId(2), SPAN, LocalId(1))
    target = _typed(answers, e.Field(ShirId(1), SPAN, base, "name", SPAN), STRING)
    value = _typed(answers, e.Lit(ShirId(3), SPAN, e.StrConst("towel")), STRING)
    stmt = s.Assign(ShirId(4), SPAN, target, value, False)
    assert isinstance(target, e.Field)
    _stops(agg.field_write, "owning field write", _low(answers), stmt, target)


def test_a_match_on_a_tuple_that_owns_stops() -> None:
    answers = TypeckResults()
    pair = StructType(name="$Tuple<i32, string>", fields=(("0", I32), ("1", STRING)),
                      generic_base="$Tuple", generic_args=(I32, STRING))
    scrutinee = _typed(answers, e.LocalRef(ShirId(2), SPAN, LocalId(1)), pair)
    stmt = s.Match(ShirId(1), SPAN, scrutinee, [], False)
    answers.record_match(stmt.shir_id, MatchFacts(pair, None))
    _stops(lower_match.match, "Match owning", _low(answers, (pair,)), stmt)
