"""The Pass 1.5 lambda annotate seam must not mutate the node (issue #214).

Pass 1.5 types a lambda *argument* to infer a generic call's type parameters, but it runs
BEFORE Pass 2's expected-type propagation. `infer_lambda_type(..., stamp=True)` (Pass 2's
form) memoizes `resolved_type` and persists param/capture types on the node; doing that at
Pass 1.5 would freeze an under-resolved type that Pass 2 and lambda-lift then read back --
the regression that broke every higher-order test the last time this was attempted. The
`stamp=False` seam computes the same FunctionType with no node mutation and restores the
scope table. This test pins exactly that.
"""
from __future__ import annotations

from sushi_lang.semantics.passes.types.visitor import infer_lambda_type
from sushi_lang.semantics.ast import Lambda, Param, Name
from sushi_lang.semantics.typesys import BuiltinType, FunctionType


class _StubValidator:
    """Minimal stand-in: infer_lambda_type only needs a scope dict and a body inferrer."""
    def __init__(self):
        self.variable_types = {}

    def infer_expression_type(self, expr):
        # The lambda body here is the param `x` (typed i32); return its type.
        return self.variable_types.get(getattr(expr, "id", None))


def _typed_lambda():
    # |i32 x| x  -- an expression-body, typed-param lambda.
    return Lambda(
        loc=None,
        params=[Param(name="x", ty=BuiltinType.I32)],
        body=Name(loc=None, id="x"),
        is_block_body=False,
    )


def test_stamp_false_does_not_mutate_the_node():
    lam = _typed_lambda()
    v = _StubValidator()

    ft = infer_lambda_type(v, lam, stamp=False)

    assert isinstance(ft, FunctionType)
    assert ft.param_types == (BuiltinType.I32,)
    assert ft.ok_type == BuiltinType.I32
    # The seam: no annotation was written back onto the node.
    assert lam.resolved_type is None
    # The scope table is restored (lambda params are local to the read-only inference).
    assert v.variable_types == {}


def test_stamp_true_does_memoize():
    lam = _typed_lambda()
    v = _StubValidator()

    ft = infer_lambda_type(v, lam, stamp=True)

    # Pass 2's form persists the result for the lift pass / backend.
    assert lam.resolved_type is ft
