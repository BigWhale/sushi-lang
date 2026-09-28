"""A math family call with refused arguments is one diagnostic per argument (#907).

The family answers the type of its row, and a first argument outside the family has no
row. The call then has no type, so the `let` that receives it adds no CE2002.
This module checks the family table: it answers no type for an argument outside its
rows.
"""
from __future__ import annotations


def test_the_family_answers_no_type_outside_its_rows():
    from sushi_lang.semantics.typesys import BuiltinType
    from sushi_lang.sushi_stdlib.src.math import get_builtin_math_function_return_type

    returns = get_builtin_math_function_return_type
    assert returns("max", [BuiltinType.U8, BuiltinType.U8]) == BuiltinType.U8
    assert returns("abs", [BuiltinType.U8]) is None
    assert returns("max", [BuiltinType.STRING, BuiltinType.STRING]) is None
    assert returns("max", []) is None
