"""Method-level type parameters on extensions: the pins under the resolution change.

Phase 4 of the UFCS epic leans on three existing behaviours and one new helper. The
pins land BEFORE the resolution change (risks 6/7): if `unify_types` or
`substitute_type_params` ever stops covering the nested-UnknownType shapes, the
method-generic inference breaks silently -- these tests name the dependency.
"""
from __future__ import annotations

from sushi_lang.semantics.generics.types import TypeParameter, substitute_type_params
from sushi_lang.semantics.generics.unify import unify_types
from sushi_lang.semantics.typesys import BuiltinType, FunctionType, UnknownType


# -- unify_types: the fn(T) -> U shape solves both parameters ----------------------

def test_unify_binds_nested_unknowns_inside_a_function_type():
    param = FunctionType(param_types=(UnknownType(name="T"),),
                         ok_type=UnknownType(name="U"),
                         err_type=UnknownType(name="StdError"))
    arg = FunctionType(param_types=(BuiltinType.I32,),
                       ok_type=BuiltinType.BOOL,
                       err_type=UnknownType(name="StdError"))

    mapping: dict = {}
    assert unify_types(param, arg, mapping)
    assert mapping["T"] == BuiltinType.I32
    assert mapping["U"] == BuiltinType.BOOL


def test_unify_binds_a_type_parameter_from_a_plain_argument():
    mapping: dict = {}
    assert unify_types(TypeParameter(name="U"), BuiltinType.STRING, mapping)
    assert mapping["U"] == BuiltinType.STRING


# -- substitute_type_params: an UnknownType substitutes by NAME ---------------------

def test_substitute_replaces_unknown_type_by_name():
    out = substitute_type_params(UnknownType(name="U"), {"U": BuiltinType.BOOL})
    assert out == BuiltinType.BOOL


def test_substitute_reaches_a_nested_function_type():
    ft = FunctionType(param_types=(UnknownType(name="T"),),
                      ok_type=UnknownType(name="U"),
                      err_type=UnknownType(name="StdError"))
    out = substitute_type_params(ft, {"T": BuiltinType.I32, "U": BuiltinType.BOOL})
    assert out.param_types == (BuiltinType.I32,)
    assert out.ok_type == BuiltinType.BOOL


# -- extension_symbol: one symbol for declaration, call site, and dedup -------------

def test_extension_symbol_without_margs_is_the_historical_symbol():
    from sushi_lang.semantics.generics.name_mangling import extension_symbol

    assert extension_symbol("i32", "squared") == "i32_squared"
    assert extension_symbol("List<i32>", "counted") == "List__i32_counted"
    assert extension_symbol("i32[]", "sum") == "arr__i32_sum"


def test_extension_symbol_suffixes_margs_only_when_present():
    from sushi_lang.semantics.generics.name_mangling import extension_symbol

    one = extension_symbol("List<i32>", "mapv", (BuiltinType.BOOL,))
    two = extension_symbol("List<i32>", "mapv", (BuiltinType.BOOL, BuiltinType.I64))
    assert one == "List__i32_mapv__bool"
    assert two == "List__i32_mapv__bool_i64"


def test_extension_symbol_distinct_margs_never_collide():
    """Two different U's on ONE receiver are two symbols (the weak_odr dedup keeps
    one DEFINITION per symbol, so distinctness here is what keeps both bodies)."""
    from sushi_lang.semantics.generics.name_mangling import extension_symbol

    a = extension_symbol("List<i32>", "mapv", (BuiltinType.BOOL,))
    b = extension_symbol("List<i32>", "mapv", (BuiltinType.STRING,))
    assert a != b
