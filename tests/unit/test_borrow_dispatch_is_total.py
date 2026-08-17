"""The borrow checker must have an arm for every expression node. No silent skips."""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.passes.borrow import BorrowChecker, INERT_EXPRS


def _expr_union_members() -> set[str]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return {t.__name__ for t in typing.get_args(sushi_ast.Expr)}


def _dispatched_names() -> set[str]:
    """Every class name `_check_expr` tests with isinstance(), plus the inert tuple."""
    src = inspect.getsource(BorrowChecker._check_expr)
    tree = ast.parse(textwrap.dedent(src))

    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance"):
            continue
        target = node.args[1]
        if isinstance(target, ast.Name):
            names.add(target.id)          # isinstance(expr, Name)
        elif isinstance(target, ast.Tuple):
            for elt in target.elts:        # isinstance(expr, (A, B))
                if isinstance(elt, ast.Name):
                    names.add(elt.id)

    # `INERT_EXPRS` is referenced by name in the source; resolve it to its members.
    if "INERT_EXPRS" in names:
        names.discard("INERT_EXPRS")
        names |= {t.__name__ for t in INERT_EXPRS}

    return names


def test_every_expression_node_has_an_arm():
    missing = sorted(_expr_union_members() - _dispatched_names())
    assert not missing, (
        f"BorrowChecker._check_expr has no arm for: {missing}.\n"
        "An expression node with no arm gets NO borrow checking -- silently. Add a real "
        "arm, or add it to INERT_EXPRS if it genuinely owns nothing and names nothing."
    )


def test_no_arm_names_a_node_outside_the_expr_union():
    """The mirror: an arm for a node that is not an Expr is dead code or a typo."""
    known = _expr_union_members() | {
        # Non-Expr types legitimately tested inside _check_expr's arms.
        "Pattern", "str",
    }
    stray = sorted(_dispatched_names() - known)
    assert not stray, f"_check_expr dispatches on non-Expr node(s): {stray}"


def test_inert_exprs_really_are_leaves():
    """An 'inert' node must have no sub-expression fields -- else we are skipping a subtree."""
    expr_members = _expr_union_members()
    for node_type in INERT_EXPRS:
        hints = typing.get_type_hints(node_type, globalns=vars(sushi_ast))
        for field, hint in hints.items():
            referenced = {
                t.__name__ for t in typing.get_args(hint) if hasattr(t, "__name__")
            } | ({hint.__name__} if hasattr(hint, "__name__") else set())
            assert not (referenced & expr_members), (
                f"{node_type.__name__} is in INERT_EXPRS but its field '{field}' holds "
                f"an expression ({hint}). It is not a leaf -- give it a real arm."
            )


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
