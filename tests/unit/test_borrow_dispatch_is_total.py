"""The borrow checker must have an arm for every expression node. No silent skips.

`BorrowChecker._check_expr` is an if/elif chain. Its `else` used to be an implicit
`pass`, which meant that an expression node with no arm got **no borrow checking at
all** -- silently, forever. That is a soundness failure mode, not a crash, so nothing
caught it: three bugs lived in that gap at once.

  #174  Spread     -- `f(arr...)` never marked its source moved  -> use-after-free, SIGSEGV
  #175  RangeExpr  -- `0..xs.len()` never visited its bounds     -> missed use-after-move
  #176  (run())    -- perk_impls never walked                    -> perk bodies unchecked

`Expr` (semantics/ast.py) is an explicit Union, so "every expression node" is an
enumerable set. This test pins that set against what `_check_expr` actually dispatches
on. Adding a member to the Union without teaching the borrow checker about it turns CI
red, instead of silently disabling borrow checking for it.

An arm may be a real check or an explicit entry in `_INERT_EXPRS` (a literal owns
nothing and names nothing). What it may NOT be is absent.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.passes.borrow import BorrowChecker, _INERT_EXPRS


def _expr_union_members() -> set[str]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return {t.__name__ for t in typing.get_args(sushi_ast.Expr)}


def _dispatched_names() -> set[str]:
    """Every class name `_check_expr` tests with isinstance(), plus the inert tuple.

    Read statically from the source: an isinstance() arm is the dispatch mechanism, so
    the source IS the contract. A dynamic probe would need a valid instance of every
    node type, which is far more brittle than parsing 40 lines of if/elif.
    """
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

    # `_INERT_EXPRS` is referenced by name in the source; resolve it to its members.
    if "_INERT_EXPRS" in names:
        names.discard("_INERT_EXPRS")
        names |= {t.__name__ for t in _INERT_EXPRS}

    return names


def test_every_expression_node_has_an_arm():
    missing = sorted(_expr_union_members() - _dispatched_names())
    assert not missing, (
        f"BorrowChecker._check_expr has no arm for: {missing}.\n"
        "An expression node with no arm gets NO borrow checking -- silently. Add a real "
        "arm, or add it to _INERT_EXPRS if it genuinely owns nothing and names nothing."
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
    """An 'inert' node must have no sub-expression fields -- else we are skipping a subtree.

    This is the load-bearing half of the allowlist. If someone gives a literal an
    expression-typed field and leaves it in _INERT_EXPRS, borrow checking silently stops
    at it, which is exactly the bug class this file exists to prevent.
    """
    expr_members = _expr_union_members()
    for node_type in _INERT_EXPRS:
        hints = typing.get_type_hints(node_type, globalns=vars(sushi_ast))
        for field, hint in hints.items():
            referenced = {
                t.__name__ for t in typing.get_args(hint) if hasattr(t, "__name__")
            } | ({hint.__name__} if hasattr(hint, "__name__") else set())
            assert not (referenced & expr_members), (
                f"{node_type.__name__} is in _INERT_EXPRS but its field '{field}' holds "
                f"an expression ({hint}). It is not a leaf -- give it a real arm."
            )


def test_run_walks_every_declaration_that_holds_a_body():
    """#176: run() skipped perk_impls, so perk bodies were never checked.

    Pin the set of Program collections the borrow checker walks against the ones that
    actually carry executable bodies. A new declaration kind with a body must be walked.
    """
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
