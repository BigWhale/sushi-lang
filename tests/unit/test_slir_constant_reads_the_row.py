"""SLIR lowering makes the constant of a literal and of a name of a constant from the rows of typecheck (docs/design/ir.md 8.5).

An integer literal takes the value that typecheck wrote for it (ruling R-W), not the value
that it spells. A `ConstRef` takes the value of the constant, and a string constant is a
literal that owns no heap. The tests call the lowering functions over data; they do not run
the compiler. A name of a struct, an enum or an array constant, and a name of a constant
whose value its type cannot hold, stop the lowering of the body.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import StageStop
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import AggregateConstant, ConstantValue, ScalarConstant
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir.lower import _Lowering
from sushi_lang.semantics.typesys import ArrayType, BuiltinType

SPAN = Span(3, 5, 3, 12)
U8 = BuiltinType.U8
I8 = BuiltinType.I8


def _literal(const: e.Constant, ty: BuiltinType, held: int | None = None) -> tuple[e.Lit, TypeckResults]:
    lit = e.Lit(ShirId(1), SPAN, const)
    answers = TypeckResults()
    answers.record_type(lit.shir_id, ty)
    if held is not None:
        answers.record_value(lit.shir_id, ScalarConstant(held, ty))
    return lit, answers


@pytest.mark.parametrize(("const", "ty", "held"), [
    (e.IntConst(300), U8, 44),
    (e.IntConst(0xFF, radix=16), I8, -1),
    (e.IntConst(128), I8, -128),
], ids=["cast", "hexadecimal", "negation"])
def test_an_integer_literal_takes_its_value_row(const: e.IntConst, ty: BuiltinType,
                                                held: int) -> None:
    lit, answers = _literal(const, ty, held)
    assert k.of_literal(lit, answers) == k.Int(held, ty)


def test_an_integer_literal_with_no_value_row_is_an_internal_error() -> None:
    lit, answers = _literal(e.IntConst(7), U8)
    with pytest.raises(Exception, match="no value reached SLIR"):
        k.of_literal(lit, answers)


@pytest.mark.parametrize(("value", "constant"), [
    (ScalarConstant(44, U8), k.Int(44, U8)),
    (ScalarConstant(0.5, BuiltinType.F64), k.Float(0.5, BuiltinType.F64)),
    (ScalarConstant(True, BuiltinType.BOOL), k.Bool(True)),
    (ScalarConstant("Mostly Harmless", BuiltinType.STRING), k.Str(b"Mostly Harmless")),
], ids=["integer", "float", "bool", "string"])
def test_a_scalar_value_is_its_constant(value: ScalarConstant, constant: k.Constant) -> None:
    assert k.of_scalar(value) == constant


def _constant_of(value: ConstantValue) -> k.Constant:
    ref = e.ConstRef(ShirId(2), SPAN, DeclNames().refer("STEP"))
    answers = TypeckResults()
    answers.record_value(ref.shir_id, value)
    return _Lowering.constant(SimpleNamespace(typeck=answers), ref)  # type: ignore[arg-type]


def test_a_name_of_a_constant_takes_its_value_row() -> None:
    assert _constant_of(ScalarConstant(-128, I8)) == k.Int(-128, I8)
    assert _constant_of(ScalarConstant("Mostly Harmless", BuiltinType.STRING)) == \
        k.Str(b"Mostly Harmless")


@pytest.mark.parametrize(("value", "construct"), [
    (AggregateConstant([ScalarConstant(1, I8)], ArrayType(I8, 1)), "aggregate constant"),
    (ScalarConstant(255, I8), "ConstRef out of range"),
], ids=["aggregate", "out of range"])
def test_a_name_of_a_constant_with_no_constant_kind_stops(value: ConstantValue,
                                                          construct: str) -> None:
    with pytest.raises(StageStop) as caught:
        _constant_of(value)
    assert caught.value.params == {"stage": "slir", "construct": construct}
    assert caught.value.span == SPAN
