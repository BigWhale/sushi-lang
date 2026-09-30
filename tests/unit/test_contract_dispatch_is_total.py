"""The contract reader answers every type kind, for each of `Eq`, `Ord` and `Display`.

`contract_of` is the one reader of "can a derived equality, order or string form read a
value of this type". A kind it had no answer for would be read as accepted, and the
backend would then meet a value it has no function for. The gate is the one the hash
reader has (`test_hashability_dispatch_is_total.py`), parametrised over the three
contracts.
"""
from __future__ import annotations

import typing

import pytest

from sushi_lang.semantics import typesys
from sushi_lang.semantics.generics.contracts import (
    ACCEPTED_KINDS, CONTRACTS, REFUSED_KINDS, WALKED_KINDS, contract_of, operand_contract)
from sushi_lang.semantics.generics.types import (
    GenericEnumType, GenericStructType, GenericTypeRef, TypePack, TypeParameter)
from sushi_lang.semantics.typesys import (
    BuiltinType, EnumType, EnumVariantInfo, ForeignPtrType, FunctionType, IteratorType,
    ReferenceType, StructType)

OFF_UNION_KINDS = {"GenericStructType", "GenericEnumType", "TypePack"}


def _one_of_every_refused_kind() -> dict[str, object]:
    plain = BuiltinType.I32
    return {
        "ForeignPtrType": ForeignPtrType(),
        "FunctionType": FunctionType(param_types=(plain,), ok_type=plain, err_type=plain),
        "ReferenceType": ReferenceType(plain, typesys.BorrowMode.PEEK),
        "IteratorType": IteratorType(element_type=plain),
        "TypeParameter": TypeParameter("T"),
        "TypePack": TypePack(types=(plain,)),
        "GenericTypeRef": GenericTypeRef(base_name="Box", type_args=(plain,)),
        "GenericStructType": GenericStructType(
            name="G", type_params=(TypeParameter("T"),), fields=(("f", plain),)),
        "GenericEnumType": GenericEnumType(
            name="GE", type_params=(TypeParameter("T"),),
            variants=(EnumVariantInfo(name="V", associated_types=(plain,)),)),
    }


def test_every_type_kind_has_an_answer():
    union = {t.__name__ for t in typing.get_args(typesys.Type)}
    covered = set(REFUSED_KINDS) | WALKED_KINDS | ACCEPTED_KINDS | {"UnknownType"}
    assert sorted((union | OFF_UNION_KINDS) - covered) == []


def test_no_kind_is_named_twice():
    named = [set(REFUSED_KINDS), set(WALKED_KINDS), set(ACCEPTED_KINDS)]
    overlap = set()
    for i, left in enumerate(named):
        for right in named[i + 1:]:
            overlap |= left & right
    assert overlap == set()


@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("kind", sorted(_one_of_every_refused_kind()))
def test_a_refused_kind_actually_refuses(contract, kind):
    answer, reason = contract_of(_one_of_every_refused_kind()[kind], contract)
    assert not answer and reason


@pytest.mark.parametrize("contract", CONTRACTS)
def test_a_fn_field_refuses_and_names_the_field(contract):
    handler = StructType(name="Handler", fields=(
        ("id", BuiltinType.I32),
        ("f", FunctionType(param_types=(BuiltinType.I32,), ok_type=BuiltinType.I32,
                           err_type=None))))
    answer, reason = contract_of(handler, contract)
    assert not answer
    assert "field 'f' -> a function value" in reason


@pytest.mark.parametrize("contract", CONTRACTS)
def test_the_override_is_terminal(contract):
    handler = StructType(name="Handler", fields=(
        ("f", FunctionType(param_types=(BuiltinType.I32,), ok_type=BuiltinType.I32,
                           err_type=None)),))
    holder = StructType(name="Holder", fields=(("h", handler),))
    answer, _ = contract_of(holder, contract, overridden=lambda ty: ty == handler)
    assert answer


@pytest.mark.parametrize("contract", CONTRACTS)
def test_a_recursive_type_is_accepted(contract):
    """One function per type: a nested value of the same type is a call of it."""
    chain = EnumType(name="Chain", variants=[EnumVariantInfo(name="End", associated_types=())])
    link = EnumVariantInfo(name="Link", associated_types=(chain,))
    chain.variants.append(link)
    assert contract_of(chain, contract)[0]


def test_the_top_level_keeps_the_primitive_sets():
    assert operand_contract(BuiltinType.BOOL, "Eq")[0]
    assert not operand_contract(BuiltinType.BOOL, "Ord")[0]
    assert operand_contract(BuiltinType.STRING, "Ord")[0]
    assert not operand_contract(BuiltinType.BLANK, "Display")[0]
