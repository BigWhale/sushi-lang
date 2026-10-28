"""The typecheck arm of a `match` on an enum, and a variant in a comparison (docs/design/ir.md 7.3, 7.8).

A match on an enum records its `MatchFacts` with no integer type, checks each arm, gives
each binder the type of its payload position with no `peek` or `poke` (the mode is in the
`PatBinding`), walks the body of the arm, and runs the one checker over the rows of the
arms. The alternatives of `|` that bind one name share one local, which gets one row. A
`Maybe` or a `Result` variant in a comparison takes the type of the other operand. The
tests run the arms over SHIR built by hand, with a stand-in for the pass; they do not run
the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.report import Span
from sushi_lang.semantics.passes.types import visit
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.body import ShirLocal
from sushi_lang.semantics.shir.ids import DeclId, LocalId, ShirId
from sushi_lang.semantics.shir.nodes import ShirBlock
from sushi_lang.semantics.shir.typeck import MatchFacts, TypeckResults
from sushi_lang.semantics.typesys import (
    BorrowMode, BuiltinType, EnumType, EnumVariantInfo)

SPAN = Span(6, 5, 6, 30)
I32, F64, BOOL = BuiltinType.I32, BuiltinType.F64, BuiltinType.BOOL
VALUE = EnumType("Value", (EnumVariantInfo("Int", (I32,)), EnumVariantInfo("Wide", (I32,)),
                           EnumVariantInfo("Float", (F64,))))
MAYBE_I32 = EnumType("Maybe<i32>", (EnumVariantInfo("Some", (I32,)),
                                    EnumVariantInfo("None", ())),
                     generic_base="Maybe", generic_args=(I32,))
_DECLS = {DeclId(0): "Value", DeclId(1): "Maybe"}


def _validator(answers: TypeckResults, locals_: list[ShirLocal]) -> SimpleNamespace:
    empty = SimpleNamespace(by_name={})
    return SimpleNamespace(
        body_answers=lambda: answers, decl_name=lambda decl: _DECLS[decl],
        struct_table=empty, enum_table=SimpleNamespace(by_name={"Value": VALUE}),
        generic_enum_table=SimpleNamespace(by_name={"Maybe": object()}),
        visibility=None, reporter=None, uncovered_matches=set(),
        body_locals={local.id: local for local in locals_}, errors_offered=lambda: 0,
        current_unit_name="main", in_template_check=False, in_synthesized_body=False,
        derived_methods=None)


def _variant(ident: int, variant: str, items: list) -> p.Variant:
    return p.Variant(ShirId(ident), SPAN, DeclId(0), variant, items, SPAN, SPAN)


def _arm(index: int, pattern: p.ShirPattern) -> p.Arm:
    return p.Arm(pattern, ShirBlock(ShirId(100 + index), SPAN, []))


def _match(arms: list[p.Arm], locals_: list[ShirLocal]):
    answers = TypeckResults()
    answers.record_local(LocalId(0), VALUE)
    node = s.Match(ShirId(1), SPAN, e.LocalRef(ShirId(2), SPAN, LocalId(0)), arms, False)
    return node, answers, _validator(answers, locals_)


def test_an_enum_match_records_its_facts_and_types_each_binder() -> None:
    x, y = LocalId(1), LocalId(2)
    node, answers, validator = _match([
        _arm(0, p.Or(ShirId(10), SPAN, [_variant(11, "Int", [p.PatBinding(x, s.Bare())]),
                                        _variant(12, "Wide", [p.PatBinding(x, s.Bare())])])),
        _arm(1, _variant(13, "Float", [p.PatBinding(y, s.Ref(BorrowMode.POKE))])),
    ], [ShirLocal(x, "x", None, SPAN), ShirLocal(y, "y", None, SPAN)])
    visit.check(validator, node)
    assert answers.match_facts_of(node.shir_id) == MatchFacts(VALUE, None)
    assert answers.local_type(x) == I32
    assert answers.local_type(y) == F64
    assert validator.uncovered_matches == set()


def test_an_enum_match_that_misses_a_variant_is_not_covered() -> None:
    node, answers, validator = _match([
        _arm(0, _variant(10, "Int", [p.Wildcard(ShirId(11), SPAN)])),
    ], [])
    codes: list[str] = []
    validator.reporter = SimpleNamespace(
        error=lambda code, _text, _span, filename=None: codes.append(code))
    visit.check(validator, node)
    assert answers.match_facts_of(node.shir_id) == MatchFacts(VALUE, None)
    assert validator.uncovered_matches == {node.shir_id}
    assert codes == ["CE2040"]


@pytest.mark.parametrize("flipped", [False, True])
def test_a_wrapper_variant_takes_the_type_of_the_other_operand(flipped: bool) -> None:
    answers = TypeckResults()
    answers.record_local(LocalId(0), MAYBE_I32)
    local = e.LocalRef(ShirId(2), SPAN, LocalId(0))
    none = e.EnumInit(ShirId(3), SPAN, DeclId(1), "None", [], SPAN)
    lhs, rhs = (none, local) if flipped else (local, none)
    node = e.Binary(ShirId(4), SPAN, "==", lhs, rhs)
    assert visit.check_expr(_validator(answers, []), node) == BOOL
    assert answers.instance_of(none.shir_id) == MAYBE_I32
