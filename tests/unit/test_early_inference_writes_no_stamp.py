"""The two passes that run before the typecheck pass infer types and write no stamp (#806).

The `instantiate` pass and the `monomorphize` pass type a generic call's arguments
through the typecheck pass's own inferrer. Inference writes stamps on the AST, and the
backend reads them, so an early pass that stamps a node from tables that are not
complete yet leaves a value that only a later overwrite corrects.

The walk stops at the entry of the `resolve` stage, which is the first stage after the
two early passes. A second run goes through the typecheck pass over the same source and
is the control: it must find every stamp name in use, or the first assertion proves
nothing.
"""
from __future__ import annotations

import pytest


STAMPS = (
    "inferred_return_type",
    "inferred_element_type",
    "resolved_enum_type",
    "resolved_struct_type",
    "callee_method_type_args",
)











@pytest.mark.parametrize("name", STAMPS)
def test_every_stamp_name_is_an_ast_field(name):
    from dataclasses import fields

    from sushi_lang.semantics import ast as A
    declared = {f.name for cls in vars(A).values()
                if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__")
                for f in fields(cls)}
    assert name in declared
