"""Named types are identified by their name, never by their structure."""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo, StructType


def _struct(name: str, fields) -> StructType:
    return StructType(name=name, fields=tuple(fields))


def _enum(name: str, variants) -> EnumType:
    return EnumType(name=name, variants=tuple(variants))


class TestStructIdentity:
    def test_same_name_different_fields_are_equal(self):
        """The table entry is the authority; field depth is not part of identity."""
        unresolved = _struct("Own<Node>", [("value", BuiltinType.I32)])
        resolved = _struct("Own<Node>", [])

        assert unresolved == resolved

    def test_different_names_are_not_equal(self):
        assert _struct("Own<Node>", []) != _struct("Own<Leaf>", [])

    def test_hash_agrees_with_equality(self):
        """Equal values must hash equally, or dict lookups miss silently."""
        a = _struct("Holder", [("value", BuiltinType.I32)])
        b = _struct("Holder", [])

        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_not_equal_to_other_types(self):
        assert _struct("Colour", []) != _enum("Colour", [])


class TestEnumIdentity:
    def test_same_name_different_variants_are_equal(self):
        """The CE0126 shape: one Result interned before its payloads resolved."""
        unresolved = _enum("Result<i32, StdError>", [])
        resolved = _enum("Result<i32, StdError>", [
            EnumVariantInfo(name="Ok", associated_types=(BuiltinType.I32,)),
            EnumVariantInfo(name="Err", associated_types=()),
        ])

        assert unresolved == resolved

    def test_different_names_are_not_equal(self):
        assert _enum("Maybe<i32>", []) != _enum("Maybe<string>", [])

    def test_hash_agrees_with_equality(self):
        a = _enum("Maybe<Own<Node>>", [])
        b = _enum("Maybe<Own<Node>>", [
            EnumVariantInfo(name="Some", associated_types=(BuiltinType.I32,)),
        ])

        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1


class TestStructuralTypesStayStructural:
    """Only NAMED types are nominal. Types with no declaration identity -- Go's non-`Named` types
    -- must keep comparing structurally.
    """

    def test_dynamic_array_compares_by_element(self):
        from sushi_lang.semantics.typesys import DynamicArrayType

        assert DynamicArrayType(base_type=BuiltinType.I32) == DynamicArrayType(
            base_type=BuiltinType.I32)
        assert DynamicArrayType(base_type=BuiltinType.I32) != DynamicArrayType(
            base_type=BuiltinType.STRING)

    def test_fixed_array_compares_by_element_and_size(self):
        from sushi_lang.semantics.typesys import ArrayType

        assert ArrayType(base_type=BuiltinType.I32, size=3) == ArrayType(
            base_type=BuiltinType.I32, size=3)
        assert ArrayType(base_type=BuiltinType.I32, size=3) != ArrayType(
            base_type=BuiltinType.I32, size=4)

    def test_generic_type_ref_compares_by_args(self):
        from sushi_lang.semantics.generics.types import GenericTypeRef

        assert GenericTypeRef(base_name="Own", type_args=(BuiltinType.I32,)) == GenericTypeRef(
            base_name="Own", type_args=(BuiltinType.I32,))
        assert GenericTypeRef(base_name="Own", type_args=(BuiltinType.I32,)) != GenericTypeRef(
            base_name="Own", type_args=(BuiltinType.STRING,))
