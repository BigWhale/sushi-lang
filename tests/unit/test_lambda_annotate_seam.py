"""The instantiate pass's lambda annotate seam must not mutate the node (issue #214)."""
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

    # The typecheck pass's form persists the result for the lift pass / backend.
    assert lam.resolved_type is ft
