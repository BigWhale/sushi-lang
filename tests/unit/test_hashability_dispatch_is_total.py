"""The hashability reader must have an answer for every type kind. No silent yes.

The field loop, the payload loop and the array walk each carried their own chain of
`isinstance` arms, and each chain stopped at the same five kinds. A field of any other
kind fell out of the `for` body untouched, which reads as hashable, so a struct holding
a `fn(i32) -> i32` got a derived `hash()` the backend could not emit and the user read
CE0052 -- an internal error, tier 1, about a program that was theirs to fix (#618).

One reader, and this gate says it is total.
"""
from __future__ import annotations

import typing

import pytest

from sushi_lang.semantics import typesys
from sushi_lang.semantics.generics import hashing
from sushi_lang.semantics.generics.hashing import (
    HASHABLE_KINDS,
    LET_THROUGH_KINDS,
    UNHASHABLE_KINDS,
    WALKED_KINDS,
)
from sushi_lang.semantics.generics.types import (
    GenericEnumType,
    GenericStructType,
    GenericTypeRef,
    TypeParameter,
    TypePack,
)
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    EnumVariantInfo,
    ForeignPtrType,
    FunctionType,
    IteratorType,
    ReferenceType,
    StructType,
)


# The kinds that are not in the `Type` union but do reach the walk: a generic TEMPLATE
# is a declaration, and a pack is a binding. Both hold types, so both need an answer.
OFF_UNION_KINDS = {"GenericStructType", "GenericEnumType", "TypePack"}


def _type_union_members() -> set[str]:
    return {t.__name__ for t in typing.get_args(typesys.Type)}


def _one_of_every_unhashable_kind() -> dict[str, object]:
    plain = BuiltinType.I32
    return {
        "ForeignPtrType": ForeignPtrType(),
        "FunctionType": FunctionType(
            param_types=(plain,), ok_type=plain, err_type=plain),
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


def test_every_type_kind_has_a_hashability_answer():
    # `UnknownType` is neither hashable nor walked: it is a name that did not resolve,
    # and it refuses with the name it carries.
    covered = (set(UNHASHABLE_KINDS) | set(WALKED_KINDS) | set(HASHABLE_KINDS)
               | set(LET_THROUGH_KINDS) | {"UnknownType"})
    missing = sorted((_type_union_members() | OFF_UNION_KINDS) - covered)
    assert not missing, (
        f"the hashability reader has no answer for: {missing}.\n"
        "A kind with no answer is read as hashable, so the type derives a hash() the "
        "backend cannot emit and the user reads CE0052."
    )


def test_no_kind_is_named_twice():
    named = [set(UNHASHABLE_KINDS), set(WALKED_KINDS), set(HASHABLE_KINDS),
             set(LET_THROUGH_KINDS)]
    overlap: set[str] = set()
    for i, left in enumerate(named):
        for right in named[i + 1:]:
            overlap |= left & right
    assert not overlap, f"a kind is both refused and accepted: {sorted(overlap)}"


def test_a_pointer_is_let_through_on_purpose():
    """`PointerType` is a hole, and it is named rather than left to fall through.

    It has no spelling in Sushi. It reaches the walk only in a container the compiler
    synthesizes -- `List@(T).data`, `Own@(T).value` -- and letting it through is what
    gives those two the derived hash that
    `test_method_registry_struct_builtins.test_list_monomorph_really_does_carry_a_registered_hash`
    pins. The hash cannot be emitted (`Own@(i32).hash()` reads CE0052), so refusing it
    is right, but that takes the derived hash off every container and is a ruling of
    its own.
    """
    assert set(LET_THROUGH_KINDS) == {"PointerType"}


@pytest.mark.parametrize("kind", sorted(_one_of_every_unhashable_kind()))
def test_a_refused_kind_actually_refuses(kind):
    """The reader must arrive at the refusal, not merely name it in a table."""
    value = _one_of_every_unhashable_kind()[kind]
    can_hash, reason = hashing.hashability_of(value)
    assert not can_hash, f"{kind} was read as hashable"
    assert reason, f"{kind} refused with no reason"


def test_a_fn_typed_field_refuses_and_names_the_field():
    handler = StructType(
        name="Handler",
        fields=(
            ("id", BuiltinType.I32),
            ("f", FunctionType(param_types=(BuiltinType.I32,),
                               ok_type=BuiltinType.I32, err_type=BuiltinType.I32)),
        ),
    )
    can_hash, reason = hashing.can_struct_be_hashed(handler)
    assert not can_hash
    assert "'f'" in reason and "function" in reason, reason


def test_a_fn_typed_payload_refuses_and_names_the_variant():
    callback = EnumType(
        name="Callback",
        variants=(
            EnumVariantInfo(name="None", associated_types=()),
            EnumVariantInfo(name="Some", associated_types=(
                FunctionType(param_types=(BuiltinType.I32,),
                             ok_type=BuiltinType.I32, err_type=BuiltinType.I32),)),
        ),
    )
    can_hash, reason = hashing.can_enum_be_hashed(callback)
    assert not can_hash
    assert "Some" in reason and "function" in reason, reason


def test_an_array_of_functions_refuses():
    fn_type = FunctionType(param_types=(BuiltinType.I32,),
                           ok_type=BuiltinType.I32, err_type=BuiltinType.I32)
    can_hash, _reason = hashing.can_array_be_hashed(DynamicArrayType(base_type=fn_type))
    assert not can_hash


def test_a_plain_struct_is_still_hashable():
    point = StructType(name="Point",
                       fields=(("x", BuiltinType.I32), ("y", BuiltinType.I32)))
    assert hashing.can_struct_be_hashed(point) == (True, "all fields are hashable")
    assert hashing.can_array_be_hashed(ArrayType(base_type=point, size=2))[0]
