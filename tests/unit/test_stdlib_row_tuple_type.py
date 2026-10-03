"""A tuple in a stdlib row crosses as ONE layout, and a row type with no layout is CE0145.

`llvm_value_type` maps a row's Sushi type to the LLVM type it crosses as. A tuple maps
through `get_tuple_type`, the helper the generators also use, so the call site and the
generated function cannot disagree. A type the function cannot map is the internal error
CE0145, which names the TYPE: the method or the function is known.
"""
from __future__ import annotations

import pytest
from llvmlite import ir

from sushi_lang.backend.expressions.calls.stdlib.signatures import llvm_value_type
from sushi_lang.internals.errors import InternalCompilerError
from sushi_lang.semantics.generics.tuples import tuple_ref
from sushi_lang.semantics.typesys import BuiltinType, UnknownType
from sushi_lang.sushi_stdlib.src.type_definitions import get_string_type, get_tuple_type


def test_a_tuple_row_type_is_the_tuple_helper():
    pair = tuple_ref((BuiltinType.I32, BuiltinType.STRING))
    assert llvm_value_type(pair) == get_tuple_type([ir.IntType(32), get_string_type()])


def test_a_tuple_with_an_unmappable_element_has_no_llvm_type():
    assert llvm_value_type(tuple_ref((BuiltinType.I32, UnknownType("Mostly")))) is None


def test_an_unmappable_string_row_type_is_ce0145():
    from sushi_lang.backend.expressions.calls.stdlib.strings import string_method_function_type
    from sushi_lang.sushi_stdlib.src.collections.strings import MethodSpec

    spec = MethodSpec("string.harmless", (UnknownType("Mostly"),), BuiltinType.I32)
    with pytest.raises(InternalCompilerError) as raised:
        string_method_function_type(spec)
    assert raised.value.code == "CE0145"
