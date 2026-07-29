"""Named types are identified by their name, never by their structure.

Sushi's type system is nominal. A `StructType`/`EnumType` IS its interned name --
which already encodes (declaration, type arguments), because every monomorphized
generic gets a unique mangled name (`Own<Node>`, `Maybe<i32>`). The struct/enum
table is the sole authority for a named type's fields/variants.

`__hash__` has always been name-only ("struct names must be unique"). `__eq__`
used to compare fields/variants, which reintroduced structural identity through
the back door and produced a whole bug class:

  - two `Own<Node>` instances built at different field-resolution depths
    hash-matched but compared UNEQUAL, so `let Own@(Node) o = ...` reported
    `CE2002: cannot assign Own@(Node) to Own@(Node)` (#240)
  - the same shape for `EnumType` is the documented root of CE0126 -- a silent
    cache miss and a duplicate monomorphization rather than a crash
  - resolution code deep-walked struct fields *only* to make structural equality
    agree, which never terminates for a self-referential struct (#240's ICE)

Go and Rust both take the nominal route for exactly this reason: go/types compares
`*Named` by `x.Origin().obj == y.Origin().obj`, and rustc's `AdtDef` "does not
actually include the types of its fields; it includes just their DefIds".

These tests pin the invariant so the bug class cannot silently return.
"""
from __future__ import annotations

from sushi_lang.semantics.typesys import BuiltinType, EnumType, EnumVariantInfo, StructType


def _struct(name: str, fields) -> StructType:
    return StructType(name=name, fields=tuple(fields))


def _enum(name: str, variants) -> EnumType:
    return EnumType(name=name, variants=tuple(variants))


class TestStructIdentity:
    def test_same_name_different_fields_are_equal(self):
        """The table entry is the authority; field depth is not part of identity.

        This is the exact #240 shape: one `Own<Node>` carrying an unresolved
        pointee and one carrying a fully expanded one.
        """
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
    """Only NAMED types are nominal. Types with no declaration identity -- Go's
    non-`Named` types -- must keep comparing structurally."""

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
