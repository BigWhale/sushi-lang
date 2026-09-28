"""The constant evaluator decides every expression node, and its refusals are named.

`ConstantEvaluator.evaluate` was a 14-arm `isinstance` ladder with an `else` that
answered CE0108, and nothing checked that the ladder and the `Expr` union agreed: a
node kind added to the language fell through to the backstop in silence, and a kind
removed from the language left a dead arm behind (#683). The evaluator dispatches over
a table now, and the kinds that are NOT a constant are named in `NOT_CONSTANT`, so this
gate can ask the one question that matters: is every member of `Expr` in exactly one
of the two sets?

The shape is `test_borrow_dispatch_is_total.py`'s, for the same reason.
"""
from __future__ import annotations

from expr_dispatch import expr_union_types
from sushi_lang.semantics.const_eval import NOT_CONSTANT, ConstantEvaluator


def test_every_expression_node_is_decided():
    handled = set(ConstantEvaluator.HANDLERS)
    refused = set(NOT_CONSTANT)
    missing = sorted(t.__name__ for t in expr_union_types() - handled - refused)
    assert not missing, (
        f"the constant evaluator has no decision for: {missing}. Give the kind a handler, "
        "or name it in NOT_CONSTANT so its CE0108 is a decision and not a fall-through."
    )


def test_a_node_is_handled_or_refused_never_both():
    both = sorted(t.__name__ for t in set(ConstantEvaluator.HANDLERS) & set(NOT_CONSTANT))
    assert not both, f"handled and refused at once: {both}"


def test_no_decision_names_a_node_outside_the_expr_union():
    stray = sorted(t.__name__ for t in
                   (set(ConstantEvaluator.HANDLERS) | set(NOT_CONSTANT)) - expr_union_types())
    assert not stray, f"the evaluator decides on non-Expr node(s): {stray}"
