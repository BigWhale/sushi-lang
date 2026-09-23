"""The container element readers read `generic_args`, and the one splitter reads a fn type.

A `List<T>` or a `HashMap<K, V>` instance carries its type arguments in
`StructType.generic_args`. The readers used to cut the arguments out of the interned NAME,
and a function type broke the cut: its parameter list holds a comma, and the `>` of its
`->` arrow read as a closing bracket (#794). The one string splitter that is left serves
the `.slib` manifest reader, where a string really is the input.
"""
from types import SimpleNamespace

import pytest

from sushi_lang.semantics.generics.hashmap import parse_hashmap_types
from sushi_lang.semantics.generics.list import parse_list_types
from sushi_lang.semantics.generics.type_strings import split_type_arguments
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.passes.collect.enums import EnumTable
from sushi_lang.semantics.passes.collect.structs import StructTable
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, StructType


@pytest.mark.parametrize("text, parts", [
    ("string, fn(i32, i32) -> i32", ["string", "fn(i32, i32) -> i32"]),
    ("fn(fn(i32) -> i32, i32) -> i32", ["fn(fn(i32) -> i32, i32) -> i32"]),
    ("fn(i32) -> i32, i32", ["fn(i32) -> i32", "i32"]),
    ("HashMap<string, List<i32>>, i32[]", ["HashMap<string, List<i32>>", "i32[]"]),
])
def test_the_splitter_reads_a_function_type_whole(text, parts):
    assert split_type_arguments(text) == parts


def _fn(params, ok):
    return FunctionType(param_types=tuple(params), ok_type=ok, err_type=BuiltinType.I32)


def _tables(*structs):
    table = StructTable()
    for s in structs:
        table.by_name[s.name] = s
    return SimpleNamespace(struct_table=table, enum_table=EnumTable())


def test_a_hashmap_with_a_function_key_answers_both_arguments():
    key = _fn([BuiltinType.I32], BuiltinType.I32)
    hm = StructType(name=f"HashMap<{key}, i32>", fields=(), generic_base="HashMap",
                    generic_args=(key, BuiltinType.I32))
    assert parse_hashmap_types(hm, _tables()) == (key, BuiltinType.I32)


def test_a_list_of_a_nested_function_type_answers_its_element():
    inner = _fn([BuiltinType.I32], BuiltinType.I32)
    element = _fn([inner, BuiltinType.I32], BuiltinType.I32)
    lst = StructType(name=f"List<{element}>", fields=(), generic_base="List",
                     generic_args=(element,))
    assert parse_list_types(lst, _tables()) == element


def test_a_nested_generic_reference_resolves_to_its_instance():
    """The 71 corpus reads where `generic_args` held an unresolved nested reference."""
    inner = StructType(name="List<i32>", fields=(), generic_base="List",
                       generic_args=(BuiltinType.I32,))
    outer = StructType(name="List<List<i32>>", fields=(), generic_base="List",
                       generic_args=(GenericTypeRef(base_name="List",
                                                    type_args=(BuiltinType.I32,)),))
    assert parse_list_types(outer, _tables(inner)) is inner


def test_a_type_that_is_not_the_container_answers_none():
    point = StructType(name="Point", fields=())
    assert parse_list_types(point, _tables()) is None
    assert parse_hashmap_types(point, _tables()) == (None, None)


def test_the_raising_policy_refuses_a_type_that_is_not_the_container():
    from sushi_lang.internals.diagnostics import InternalCompilerError

    with pytest.raises(InternalCompilerError):
        parse_hashmap_types(StructType(name="Point", fields=()), _tables(), on_missing="raise")
