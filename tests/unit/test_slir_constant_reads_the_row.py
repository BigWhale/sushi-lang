"""SLIR lowering makes the constant of a literal and of a name of a constant from the rows of typecheck (docs/design/ir.md 8.5).

An integer literal takes the value that typecheck wrote for it (ruling R-W), not the value
that it spells. A `ConstRef` takes the value of the constant, and a string constant is a
literal that owns no heap. The tests call the lowering functions over data; they do not run
the compiler. A struct value and an array value are an `Aggregate` with no variant, also as
a payload value or a field value. A name of a constant whose value its type cannot hold
is an internal error (the R14 guard).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics.const_eval import AggregateConstant, ConstantValue, ScalarConstant
from sushi_lang.semantics.shir import exprs as e
from sushi_lang.semantics.shir.decls import DeclNames
from sushi_lang.semantics.shir.ids import ShirId
from sushi_lang.semantics.shir.typeck import TypeckResults
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.slir.lower import Lowering
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, EnumType, EnumVariantInfo, StructType,
)

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
    return Lowering.constant(SimpleNamespace(typeck=answers), ref)  # type: ignore[arg-type]


def test_a_name_of_a_constant_takes_its_value_row() -> None:
    assert _constant_of(ScalarConstant(-128, I8)) == k.Int(-128, I8)
    assert _constant_of(ScalarConstant("Mostly Harmless", BuiltinType.STRING)) == \
        k.Str(b"Mostly Harmless")


@pytest.mark.parametrize("value", [
    ScalarConstant(255, I8),
    AggregateConstant([ScalarConstant(1, I8)], ArrayType(I8, 2)),
    AggregateConstant([ScalarConstant(1, I8), ScalarConstant(300, I8)], ArrayType(I8, 2)),
], ids=["out of range", "short array", "element out of range"])
def test_a_name_of_a_constant_that_its_type_cannot_hold_is_an_internal_error(
        value: ConstantValue) -> None:
    with pytest.raises(InternalCompilerError) as caught:
        _constant_of(value)
    assert caught.value.code == "CE0000"
    assert caught.value.params == {"detail": "a constant value that its type cannot hold"}
    assert caught.value.span == SPAN


POINT = StructType(name="Point", fields=(("x", I8), ("y", U8)))
HOLDER = EnumType(name="Holder", variants=(
    EnumVariantInfo(name="Some", associated_types=(POINT,)),
    EnumVariantInfo(name="None", associated_types=())))


def test_a_struct_value_is_an_aggregate_of_its_fields_in_order() -> None:
    value = AggregateConstant([ScalarConstant(-1, I8), ScalarConstant(200, U8)], POINT)
    assert _constant_of(value) == k.Aggregate(POINT, (k.Int(-1, I8), k.Int(200, U8)))


def test_a_struct_value_in_a_payload_is_made_by_the_same_rule() -> None:
    point = AggregateConstant([ScalarConstant(3, I8), ScalarConstant(4, U8)], POINT)
    value = AggregateConstant([point], HOLDER, "Some")
    assert _constant_of(value) == k.Aggregate(
        HOLDER, (k.Aggregate(POINT, (k.Int(3, I8), k.Int(4, U8))),), "Some")


def test_an_array_value_in_a_field_is_made_by_the_same_rule() -> None:
    row = AggregateConstant([ScalarConstant(1, I8)], ArrayType(I8, 1))
    rows = StructType(name="Rows", fields=(("row", ArrayType(I8, 1)),))
    assert _constant_of(AggregateConstant([row], rows)) == k.Aggregate(
        rows, (k.Aggregate(ArrayType(I8, 1), (k.Int(1, I8),)),))
