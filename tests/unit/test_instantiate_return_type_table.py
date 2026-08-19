"""Pass 1.5's chained-call inference reads the owning family's return-type table.

Issue #269: `TypeInferrer.get_builtin_method_return_type` was a third, independent
return-type table -- it knew some string methods and Maybe, and nothing about
`to_str`, `hash` or `to_bits`. This pins that it answers exactly what the family
tables answer, so it can never drift from them again.
"""
from __future__ import annotations

import pytest

from sushi_lang.semantics.generics.instantiate.types import TypeInferrer
from sushi_lang.semantics.generics.maybe import is_builtin_maybe_method, maybe_method_return_type
from sushi_lang.semantics.generics.primitives import (
    PRIMITIVE_METHOD_RETURNS,
    primitive_method_return_type,
)
from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.sushi_stdlib.src.collections.strings import (
    METHOD_SPECS,
    get_builtin_string_method_return_type,
)

STRING_METHODS = set(METHOD_SPECS) | {"is_empty", "clone"}


def _inferrer() -> TypeInferrer:
    return TypeInferrer(variable_types={}, struct_table={}, enum_table={})


def test_every_string_method_answers_the_shared_table():
    inferrer = _inferrer()
    for method in sorted(STRING_METHODS):
        expected = get_builtin_string_method_return_type(method, BuiltinType.STRING)
        assert expected is not None, (
            f"string.{method}() has no entry in the string family table -- the table "
            f"must be total over METHOD_SPECS so no caller needs a private copy")
        got = inferrer.get_builtin_method_return_type(BuiltinType.STRING, method)
        assert got == expected, (
            f"string.{method}(): the instantiation-collection table answers {got!r}, "
            f"the string family table answers {expected!r}")


def test_the_shared_string_table_is_total_over_maybe_returning_methods():
    """find/find_last/to_i32/to_i64/to_f64 return Maybe -- the shared table used to
    answer None for them and every caller kept a private copy of the arm."""
    cases = {
        "find": BuiltinType.I32,
        "find_last": BuiltinType.I32,
        "to_i32": BuiltinType.I32,
        "to_i64": BuiltinType.I64,
        "to_f64": BuiltinType.F64,
    }
    for method, payload in cases.items():
        ret = get_builtin_string_method_return_type(method, BuiltinType.STRING)
        assert isinstance(ret, GenericTypeRef) and ret.base_name == "Maybe", (
            f"string.{method}() must spell its return as Maybe, got {ret!r}")
        assert ret.type_args == (payload,)


def test_every_primitive_method_answers_the_primitives_table():
    inferrer = _inferrer()
    for method, carriers in PRIMITIVE_METHOD_RETURNS.items():
        for receiver in carriers:
            if receiver == BuiltinType.STRING:
                continue  # the string family table owns the string receiver
            expected = primitive_method_return_type(receiver, method)
            got = inferrer.get_builtin_method_return_type(receiver, method)
            assert got == expected, (
                f"{receiver}.{method}(): the instantiation-collection table answers "
                f"{got!r}, the primitives table answers {expected!r}")


def test_maybe_methods_answer_through_the_maybe_family():
    inferrer = _inferrer()
    maybe_i32 = GenericTypeRef(base_name="Maybe", type_args=(BuiltinType.I32,))
    for method in ("is_some", "is_none", "realise", "expect"):
        assert is_builtin_maybe_method(method)
        expected = maybe_method_return_type(BuiltinType.I32, method)
        got = inferrer.get_builtin_method_return_type(maybe_i32, method)
        assert got == expected

    assert inferrer.get_builtin_method_return_type(maybe_i32, "not_a_method") is None
