"""A struct or a tuple constant goes through the one converter of an aggregate constant (docs/design/ir.md 8.5).

The SLIR emitter and the declaration round of a global both call `constant_initializer`.
A struct is one LLVM constant for each field, in the declaration order; a struct field and
a variant field are made by the same converter. The one check of a struct refuses a part of
another type than its field with an internal error. The tests call the converter over
data; they do not run the compiler.
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.constants.enum_initializers import constant_initializer, constant_of
from sushi_lang.backend.types.core import LLVMTypeSystem
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.const_eval import AggregateConstant, ScalarConstant
from sushi_lang.semantics.slir import constants as k
from sushi_lang.semantics.typesys import (
    ArrayType, BuiltinType, EnumType, EnumVariantInfo, StructType,
)

I32, U8, BOOL, F64 = BuiltinType.I32, BuiltinType.U8, BuiltinType.BOOL, BuiltinType.F64
POINT = StructType(name="Point", fields=(("x", I32), ("y", I32)))
SIGN = EnumType(name="Sign", variants=(EnumVariantInfo(name="Plus", associated_types=()),
                                       EnumVariantInfo(name="Minus", associated_types=())))
BOX = StructType(name="Box", fields=(("lo", POINT), ("on", BOOL), ("scale", F64),
                                     ("tag", U8), ("sign", SIGN)))
PAIR = StructType(name="$Tuple<i32, string>", fields=(("0", I32), ("1", BuiltinType.STRING)),
                  generic_base="$Tuple", generic_args=(I32, BuiltinType.STRING))


def _point(x: int, y: int) -> k.Aggregate:
    return k.Aggregate(POINT, (k.Int(x, I32), k.Int(y, I32)))


def test_a_struct_is_one_constant_for_each_field_in_order() -> None:
    types = LLVMTypeSystem()
    box = k.Aggregate(BOX, (_point(3, -4), k.Bool(True), k.Float(2.5, F64), k.Int(200, U8),
                            k.Aggregate(SIGN, (), "Minus")))
    value = constant_initializer(box, types, None, "")
    assert value.type == types.ll_type(BOX)
    lo, on, scale, tag, sign = value.constant
    assert [field.constant for field in lo.constant] == [3, (1 << 32) - 4]
    assert (on.constant, scale.constant, tag.constant) == (1, 2.5, 200)
    assert sign.constant[0].constant == 1


def test_a_string_field_takes_the_finisher_with_the_name_of_its_field() -> None:
    types = LLVMTypeSystem()
    names: list[tuple[str, str]] = []

    def finish(text: str, name: str) -> ir.Constant:
        names.append((text, name))
        return ir.Constant(types.ll_type(BuiltinType.STRING), None)

    constant_initializer(k.Aggregate(PAIR, (k.Int(1, I32), k.text("towel"))), types, finish,
                         ".str_data.C")
    assert names == [("towel", ".str_data.C.1")]


def test_the_global_of_a_struct_value_is_its_slir_constant() -> None:
    value = AggregateConstant([ScalarConstant(1, I32), ScalarConstant(2, I32)], POINT)
    assert constant_of(value, None, None) == _point(1, 2)


def test_the_global_of_a_struct_with_an_array_field_is_one_constant_of_each_field() -> None:
    """An array value is a SLIR constant (A6): the converter writes the array field as one
    LLVM array constant of its elements."""
    row = StructType(name="Row", fields=(("row", ArrayType(I32, 2)),))
    value = AggregateConstant([AggregateConstant([ScalarConstant(1, I32), ScalarConstant(2, I32)],
                                                 ArrayType(I32, 2))], row)
    written = constant_initializer(constant_of(value, None, None), LLVMTypeSystem(), None, "")
    assert [part.constant for part in written.constant[0].constant] == [1, 2]


@pytest.mark.parametrize("constant", [
    k.Aggregate(POINT, (k.Int(1, I32),)),
    k.Aggregate(POINT, (k.Int(1, I32), k.Int(2, BuiltinType.I64))),
    k.Aggregate(I32, (k.Int(1, I32),)),
    k.Aggregate(BOX, (_point(1, 2), k.Bool(False), k.Float(1.0, F64), k.Int(1, U8),
                      k.Aggregate(SIGN, (), "Zero"))),
    k.Aggregate(PAIR, (k.Int(1, I32), k.text("x"))),
], ids=["count", "type", "no struct", "nested variant", "string with no finisher"])
def test_the_converter_refuses_a_struct_value_that_its_type_does_not_declare(
        constant: k.Aggregate) -> None:
    with pytest.raises(InternalCompilerError):
        constant_initializer(constant, LLVMTypeSystem(), None, "")
