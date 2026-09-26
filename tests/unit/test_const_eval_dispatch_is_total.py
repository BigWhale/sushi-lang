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

import typing


from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.const_eval import NOT_CONSTANT, ConstantEvaluator


def _expr_union_members() -> set[type]:
    return set(typing.get_args(sushi_ast.Expr))


def test_every_expression_node_is_decided():
    handled = set(ConstantEvaluator.HANDLERS)
    refused = set(NOT_CONSTANT)
    missing = sorted(t.__name__ for t in _expr_union_members() - handled - refused)
    assert not missing, (
        f"the constant evaluator has no decision for: {missing}. Give the kind a handler, "
        "or name it in NOT_CONSTANT so its CE0108 is a decision and not a fall-through."
    )


def test_a_node_is_handled_or_refused_never_both():
    both = sorted(t.__name__ for t in set(ConstantEvaluator.HANDLERS) & set(NOT_CONSTANT))
    assert not both, f"handled and refused at once: {both}"


def test_no_decision_names_a_node_outside_the_expr_union():
    stray = sorted(t.__name__ for t in
                   (set(ConstantEvaluator.HANDLERS) | set(NOT_CONSTANT)) - _expr_union_members())
    assert not stray, f"the evaluator decides on non-Expr node(s): {stray}"


# Every refused kind a writer can put in a `const` initializer, and the code it reads.
# `new()` and `from(...)` are refused one step earlier by the declared `T[]` (CE2015),
# `~` by the blank type (CE2032), and a `MethodCall`, an `EnumConstructor` and a `Spread`
# never arrive: the parser spells the first two as a `DotCall` and the third is an
# argument, whose `Call` is the node that answers.



