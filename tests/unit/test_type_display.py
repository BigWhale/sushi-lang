"""`display_type` renders the `@(...)` surface form for diagnostics."""
from __future__ import annotations

from sushi_lang.semantics.generics.type_display import display_type, display_type_name
from sushi_lang.semantics.typesys import (
    ArrayType,
    BuiltinType,
    DynamicArrayType,
    EnumType,
    FunctionType,
    IteratorType,
    PointerType,
    ReferenceType,
    StructType,
)
from sushi_lang.semantics.generics.types import GenericTypeRef, TypePack

I32 = BuiltinType.I32
STRING = BuiltinType.STRING


def _mono_struct(name, base, args):
    return StructType(name=name, fields=[], generic_base=base, generic_args=args)


def _mono_enum(name, base, args):
    return EnumType(name=name, variants=(), generic_base=base, generic_args=args)


def test_leaf_equals_str():
    assert display_type(I32) == "i32" == str(I32)
    assert display_type(STRING) == "string" == str(STRING)


def test_non_generic_struct_equals_str():
    pt = StructType(name="Point", fields=[])
    assert display_type(pt) == "Point" == str(pt)


def test_generic_type_ref():
    assert display_type(GenericTypeRef("Result", (I32, STRING))) == "Result@(i32, string)"


def test_monomorphized_struct_and_enum():
    assert display_type(_mono_struct("Pair<i32, string>", "Pair", (I32, STRING))) == "Pair@(i32, string)"
    assert display_type(_mono_enum("Maybe<i32>", "Maybe", (I32,))) == "Maybe@(i32)"


def test_nested_generics():
    inner = _mono_struct("List<i32>", "List", (I32,))
    outer = _mono_enum("Maybe<List<i32>>", "Maybe", (inner,))
    assert display_type(outer) == "Maybe@(List@(i32))"


def test_iterator():
    assert display_type(IteratorType(I32)) == "Iterator@(i32)"


def test_array_dynarray_recursion():
    lst = GenericTypeRef("List", (I32,))
    assert display_type(ArrayType(base_type=lst, size=3)) == "List@(i32)[3]"
    assert display_type(DynamicArrayType(base_type=lst)) == "List@(i32)[]"


def test_reference_and_pointer_recursion():
    lst = GenericTypeRef("List", (I32,))
    ref = ReferenceType(referenced_type=lst)
    assert "List@(i32)" in display_type(ref) and display_type(ref).startswith("poke ")
    assert display_type(PointerType(pointee_type=lst)) == "List@(i32)*"


def test_function_type_arrow_and_stderr_hidden():
    stderr = EnumType(name="StdError", variants=())
    fn = FunctionType(param_types=(GenericTypeRef("List", (I32,)),), ok_type=I32, err_type=stderr)
    assert display_type(fn) == "fn(List@(i32)) -> i32"


def test_function_type_shows_non_stderr_error():
    fn = FunctionType(param_types=(), ok_type=I32, err_type=EnumType(name="MyErr", variants=()))
    assert display_type(fn) == "fn() -> i32 | MyErr"


def test_type_pack():
    assert display_type(TypePack(types=(I32, STRING))) == "pack(i32, string)"


def test_fallback_metadata_less_name():
    # Struct whose identity name carries <...> but lacks structured metadata.
    box = StructType(name="Box<i32>", fields=[])
    assert display_type(box) == "Box@(i32)"
    nested = StructType(name="Result<List<i32>>", fields=[])
    assert display_type(nested) == "Result@(List@(i32))"


def test_fallback_leaves_unsafe_names_untouched():
    # A non-generic name, an unbalanced name, and a function-ish name are left alone.
    assert display_type(StructType(name="Point", fields=[])) == "Point"
    weird = StructType(name="Weird<i32", fields=[])
    assert display_type(weird) == "Weird<i32"


# --------------------------------------------------------------------------- #
# display_type_name -- the string-level entry point, for diagnostics that hold an
# already-interned name and no Type object (CE0122's recursion chain, CE0101, ...).

def test_display_type_name_converts_interned_names():
    assert display_type_name("List<i32>") == "List@(i32)"
    assert display_type_name("Result<List<i32>, StdError>") == "Result@(List@(i32), StdError)"


def test_display_type_name_passes_through_bracket_free_names():
    assert display_type_name("Point") == "Point"
    assert display_type_name("i32") == "i32"


def test_display_type_name_refuses_ambiguous_input():
    # Unbalanced brackets and function-ish names are returned untouched rather
    # than corrupted -- the `->` guard was previously unpinned.
    assert display_type_name("Weird<i32") == "Weird<i32"
    assert display_type_name("fn(i32) -> i32") == "fn(i32) -> i32"
    assert display_type_name("List<fn(i32) -> i32>") == "List<fn(i32) -> i32>"
