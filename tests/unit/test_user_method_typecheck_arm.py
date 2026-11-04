"""The typecheck arm of a user method call and of a static (docs/design/ir.md 7.3, 7.6).

The pass types an extension method and a static of a plain type: a primitive, or a struct
or an enum that is no instance of a generic type and whose name the unit did not lose. A
call through a field of a function type is a call of a function value (C2), and it stops
the walk while no extension method of the struct has the name. The tests read the two
predicates over data, with a stand-in for the pass; they do not run the compiler.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from sushi_lang.semantics.passes.types.visit import calls
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, EnumVariantInfo, FunctionType, StructType)

I32 = BuiltinType.I32
OP = FunctionType((I32,), I32, None)
HANDLER = StructType("Handler", (("op", OP), ("id", I32)))
SIGN = EnumType("Sign", (EnumVariantInfo("Plus", ()),))
BOX_I32 = StructType("Box<i32>", (("v", I32),), generic_base="Box", generic_args=(I32,))


def _validator(*, lost: Optional[str] = None, methods: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        visibility=SimpleNamespace(contested_by=lambda _kind, name, _unit: name == lost),
        current_unit_name="main",
        extension_table=SimpleNamespace(
            get_method=lambda _ty, name: object() if name in methods else None))


@pytest.mark.parametrize("ty, lost, plain", [
    (I32, None, True),
    (HANDLER, None, True),
    (SIGN, None, True),
    (BOX_I32, None, False),
    (HANDLER, "Handler", False),
])
def test_a_plain_type_is_a_primitive_or_a_declared_struct_or_enum(ty, lost, plain) -> None:
    assert calls._is_plain(_validator(lost=lost), ty) is plain


def test_a_call_through_a_function_field_is_no_method_call() -> None:
    assert calls._calls_a_function_field(_validator(), HANDLER, "op")


def test_a_method_of_the_name_wins_over_the_field() -> None:
    assert not calls._calls_a_function_field(_validator(methods=("op",)), HANDLER, "op")


def test_a_field_of_another_type_is_no_function_call() -> None:
    assert not calls._calls_a_function_field(_validator(), HANDLER, "id")
    assert not calls._calls_a_function_field(_validator(), I32, "op")
