"""infer_struct_type's IndexAccess arm reads Pass 2's inferred_element_type stamp.

The G-RESOLVE follow-up to #348: the arm used to copy the Pass 2 element-type rule
instead of reading the stamp Pass 2 already wrote, which is exactly the
one-question-several-answer-sites shape #296/#269/#273 collect. The fake codegen
below has no memory manager, so the structural re-derivation cannot run -- passing
proves the stamp path answered first.
"""
from __future__ import annotations

from types import SimpleNamespace

from sushi_lang.backend.expressions.structs import infer_struct_type
from sushi_lang.semantics.ast import IndexAccess, IntLit, Name
from sushi_lang.semantics.typesys import BuiltinType, StructType


def test_index_access_arm_answers_the_stamp():
    row = StructType(name="Row", fields=(("n", BuiltinType.I32),))
    codegen = SimpleNamespace(
        struct_table=SimpleNamespace(by_name={"Row": row}),
        enum_table=SimpleNamespace(by_name={}),
    )
    expr = IndexAccess(
        array=Name(id="rows", loc=None),
        index=IntLit(value=0, radix=10, loc=None),
        inferred_element_type=row,
        loc=None,
    )
    assert infer_struct_type(codegen, expr) is row
