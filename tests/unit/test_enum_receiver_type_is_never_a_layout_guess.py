"""The backend never reads a Result or Maybe receiver's type off its LLVM layout (#769).

Every instance of one family with the same payload size has one LLVM type: a
`Maybe@(f64)` and a `Maybe@(i64)` are both `{i32, [1 x i64]}`. A type matched on the
layout is a guess, and a wrong guess reads the payload as the other type -- a silent
wrong value, or a CE0017 at the default argument. `infer_generic_enum_type` answers from
the typecheck pass's facts alone; a receiver it cannot type is an internal error.
The fixtures under `tests/generics/receiver_layout/` pin the end behaviour.
"""
from __future__ import annotations

import inspect

import pytest
from llvmlite import ir

from sushi_lang.backend.expressions.calls import utils
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ast import IntLit


def test_no_layout_comparison_in_the_enum_receiver_reader():
    source = inspect.getsource(utils.infer_generic_enum_type)
    assert "receiver_value.type ==" not in source
    assert "enum_table.by_name.items()" not in source


def test_a_receiver_with_no_known_type_is_an_internal_error():
    layout = ir.LiteralStructType([ir.IntType(32), ir.ArrayType(ir.IntType(64), 1)])
    value = ir.Constant(layout, None)
    with pytest.raises(InternalCompilerError) as caught:
        utils.infer_generic_enum_type(object(), IntLit(value=1, loc=None), value, "Maybe<")
    assert "CE0019" in str(caught.value) or getattr(caught.value, "code", None) == "CE0019"
