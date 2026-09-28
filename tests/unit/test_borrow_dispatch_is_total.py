"""The borrow checker must have an arm for every expression node. No silent skips."""
from __future__ import annotations

import ast
import inspect
import textwrap

from expr_dispatch import dispatched_names, expr_union_names, expression_fields
from sushi_lang.semantics.passes.borrow import BorrowChecker, INERT_EXPRS
from sushi_lang.semantics.passes.borrow import expressions as borrow_expressions


def _dispatched_names() -> set[str]:
    return dispatched_names(borrow_expressions.check_expr, inert=INERT_EXPRS)


def test_every_expression_node_has_an_arm():
    missing = sorted(expr_union_names() - _dispatched_names())
    assert not missing, (
        f"BorrowChecker._check_expr has no arm for: {missing}.\n"
        "An expression node with no arm gets NO borrow checking -- silently. Add a real "
        "arm, or add it to INERT_EXPRS if it genuinely owns nothing and names nothing."
    )


def test_no_arm_names_a_node_outside_the_expr_union():
    """The mirror: an arm for a node that is not an Expr is dead code or a typo."""
    known = expr_union_names() | {
        # Non-Expr types legitimately tested inside _check_expr's arms.
        "Pattern", "str",
    }
    stray = sorted(_dispatched_names() - known)
    assert not stray, f"_check_expr dispatches on non-Expr node(s): {stray}"


def test_inert_exprs_really_are_leaves():
    """An 'inert' node must have no sub-expression fields -- else we are skipping a subtree."""
    not_leaves = [f"{t.__name__}.{field}: {hint}"
                  for t in INERT_EXPRS for field, hint in expression_fields(t)]
    assert not not_leaves, (
        f"INERT_EXPRS holds nodes with an expression field: {not_leaves}. "
        "They are not leaves -- give each a real arm.")


def test_run_walks_every_declaration_that_holds_a_body():
    """#176: run() skipped perk_impls, so perk bodies were never checked."""
    src = inspect.getsource(BorrowChecker.run)
    walked = {
        node.attr
        for node in ast.walk(ast.parse(textwrap.dedent(src)))
        if isinstance(node, ast.Attribute) and getattr(node.value, "id", None) == "program"
    }
    assert walked == {"functions", "extensions", "generic_extensions", "perk_impls"}, (
        f"BorrowChecker.run() walks {sorted(walked)}. A Program collection holding "
        "function bodies that is not walked here is NOT borrow-checked at all (#176)."
    )
