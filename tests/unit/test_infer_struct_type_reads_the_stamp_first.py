"""infer_struct_type reads the typecheck pass's stamp first, whatever the node kind is.

#624: the function dispatched on the node kind alone, and neither a `TryExpr` nor a
`Call` had an arm, so `make()??.x` and `Point(7).x` -- both legal -- stopped with
CE0067, which renders as a compiler bug. `stamped_semantic_type` is already the ONE
reader of those stamps and is total over the two kinds, so the read belongs at the top
of the dispatch. The fake codegen below has no memory manager, so no structural
reconstruction can run: an answer proves the stamp path gave it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sushi_lang.backend.expressions.structs import infer_struct_type
from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.semantics.ast import Call, IntLit, Name, TryExpr
from sushi_lang.semantics.typesys import BuiltinType, StructType


def _codegen(row: StructType) -> SimpleNamespace:
    return SimpleNamespace(
        struct_table=SimpleNamespace(by_name={"Row": row}),
        enum_table=SimpleNamespace(by_name={}),
    )


def test_try_expr_answers_the_unwrapped_stamp():
    row = StructType(name="Row", fields=(("n", BuiltinType.I32),))
    expr = TryExpr(
        expr=Call(callee=Name(id="make", loc=None), args=[], loc=None),
        inferred_unwrapped_type=row,
        loc=None,
    )
    assert infer_struct_type(_codegen(row), expr) is row


def test_call_answers_the_return_stamp():
    row = StructType(name="Row", fields=(("n", BuiltinType.I32),))
    expr = Call(
        callee=Name(id="Row", loc=None),
        args=[IntLit(value=1, radix=10, loc=None)],
        inferred_return_type=row,
        loc=None,
    )
    assert infer_struct_type(_codegen(row), expr) is row


def test_a_node_with_no_stamp_is_still_ce0067():
    row = StructType(name="Row", fields=(("n", BuiltinType.I32),))
    with pytest.raises(InternalCompilerError) as caught:
        infer_struct_type(_codegen(row), IntLit(value=1, radix=10, loc=None))
    assert "CE0067" in str(caught.value)
