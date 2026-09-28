"""Both the instantiate pass instantiation collectors must dispatch on every expression node."""
from __future__ import annotations

import pytest

from expr_dispatch import dispatched_names, expr_union_names, expression_fields
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.generics.instantiate.expressions import ExpressionScanner
from sushi_lang.semantics.generics.monomorphize.functions import FunctionMonomorphizer

_COLLECTORS = {
    "ExpressionScanner.scan_expression": ExpressionScanner.scan_expression,
    "FunctionMonomorphizer._collect_from_expr": FunctionMonomorphizer._collect_from_expr,
}

# Leaf expression nodes both collectors legitimately treat as inert (no sub-expressions
# to recurse into). `DynamicArrayNew` is an empty `T[]`; `BlankLit` is `~`.
_INERT_LEAF_NAMES = {
    "IntLit", "FloatLit", "StringLit", "BoolLit", "Name",
    "BlankLit", "DynamicArrayNew",
}


def _dispatched_names(method) -> set[str]:
    """A collector DISPATCHES on what it was handed: `isinstance(expr, Call)`."""
    return dispatched_names(method, subject_is_a_name=True)


@pytest.mark.parametrize("label", sorted(_COLLECTORS))
def test_every_collector_dispatches_on_every_expression_node(label):
    missing = sorted(expr_union_names() - _dispatched_names(_COLLECTORS[label]))
    assert not missing, (
        f"{label} has no arm for: {missing}.\n"
        "An expression node with no arm gets its nested generic instantiations "
        "silently dropped. Add a recursing arm, or an explicit leaf entry if it "
        "genuinely holds no sub-expression."
    )


@pytest.mark.parametrize("label", sorted(_COLLECTORS))
def test_no_arm_names_a_node_outside_the_expr_union(label):
    """The mirror: an arm for a non-Expr node is dead code or a typo."""
    known = expr_union_names() | {
        # InterpolatedString parts are tested with isinstance(part, str).
        "str",
    }
    stray = sorted(_dispatched_names(_COLLECTORS[label]) - known)
    assert not stray, f"{label} dispatches on non-Expr node(s): {stray}"


def test_inert_leaves_have_no_expr_fields():
    """A node treated as inert must have no sub-expression field -- else we skip a subtree."""
    not_leaves = [f"{name}.{field}: {hint}" for name in sorted(_INERT_LEAF_NAMES)
                  for field, hint in expression_fields(getattr(sushi_ast, name))]
    assert not not_leaves, (
        f"nodes treated as inert leaves hold an expression field: {not_leaves}. "
        "Give each a recursing arm instead.")
