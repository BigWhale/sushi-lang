"""A named type is terminal in both type substitutions (#802).

Type identity is nominal: the interned name of a `StructType` / `EnumType` already IS the
declaration plus its type arguments, so a substitution has nothing to put in and must give
back the SAME object. The monomorphizer's `TypeSubstitutor.substitute_type` rebuilt the
type instead, and the rebuild lost `generic_base`, `generic_args` and `home_module`, and
resolved the field types of a user struct a second time.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.generics.monomorphize.transformer import TypeSubstitutor
from sushi_lang.semantics.generics.types import (
    GenericTypeRef, TypeParameter, substitute_type_params,
)
from sushi_lang.semantics.typesys import (
    BuiltinType, DynamicArrayType, EnumType, EnumVariantInfo, StructType, UnknownType,
)

I32 = BuiltinType.I32

LIST_I32 = StructType(
    name="List<i32>",
    fields=(("len", I32), ("cap", I32), ("data", DynamicArrayType(I32))),
    generic_base="List",
    generic_args=(I32,),
)

MAYBE_I32 = EnumType(
    name="Maybe<i32>",
    variants=(EnumVariantInfo("Some", (I32,)), EnumVariantInfo("None", ())),
    generic_base="Maybe",
    generic_args=(I32,),
)

IO_ERROR = EnumType(
    name="IoError",
    variants=(EnumVariantInfo("NotFound", ()),),
    home_module="io/error",
)

# A user generic struct whose field still holds the unresolved reference: the rebuild
# resolved it, which is the field change the ticket measured on `Holder` and `Node`.
HOLDER_I32 = StructType(
    name="Holder<i32>",
    fields=(("item", GenericTypeRef("Maybe", (I32,))), ("tag", UnknownType("T"))),
    generic_base="Holder",
    generic_args=(I32,),
)

NAMED = [LIST_I32, MAYBE_I32, IO_ERROR, HOLDER_I32]
SUBSTITUTION = {"T": BuiltinType.STRING}


class _NoMonomorphizer:
    """A named type must be answered without the monomorphizer."""

    def __getattr__(self, name):
        raise AssertionError(f"a named type reached the monomorphizer ({name})")


@pytest.mark.parametrize("named", NAMED, ids=lambda t: t.name)
def test_the_monomorphizer_substitution_gives_back_the_same_object(named):
    out = TypeSubstitutor(_NoMonomorphizer()).substitute_type(named, SUBSTITUTION)
    assert out is named


@pytest.mark.parametrize("named", NAMED, ids=lambda t: t.name)
def test_the_pure_substitution_gives_back_the_same_object(named):
    assert substitute_type_params(named, SUBSTITUTION) is named


@pytest.mark.parametrize("named", NAMED, ids=lambda t: t.name)
def test_a_named_type_nested_in_an_array_is_kept(named):
    out = TypeSubstitutor(_NoMonomorphizer()).substitute_type(
        DynamicArrayType(named), SUBSTITUTION
    )
    assert out.base_type is named


def test_a_type_parameter_is_still_substituted():
    out = TypeSubstitutor(_NoMonomorphizer()).substitute_type(
        DynamicArrayType(TypeParameter("T")), SUBSTITUTION
    )
    assert out == DynamicArrayType(BuiltinType.STRING)
