"""Both Pass 1.5 instantiation collectors must dispatch on every expression node.

`ExpressionScanner.scan_expression` (the main Pass 1.5 collector) and
`FunctionMonomorphizer._collect_from_expr` (the monomorphizer's nested-call collector)
are `if/elif` chains with no `else`. A missing arm means an expression node is silently
skipped -- its nested generic instantiations are never collected, so the backend gets no
monomorphized symbol (CE2061 / CE0000). This is the same silent-skip soundness class as
the borrow-checker gaps #174/#175 (see `test_borrow_dispatch_is_total.py`), pinned here
for the two collectors (issue #214).

`Expr` (semantics/ast.py) is an explicit Union, so "every expression node" is an
enumerable set. Adding a member without teaching both collectors about it turns CI red
instead of silently dropping instantiations for it. An arm may recurse or be an explicit
leaf entry -- what it may NOT be is absent.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

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


def _expr_union_members() -> set[str]:
    return {t.__name__ for t in typing.get_args(sushi_ast.Expr)}


def _dispatched_names(method) -> set[str]:
    """Every class name a collector method tests with isinstance(), read from its source."""
    src = inspect.getsource(method)
    tree = ast.parse(textwrap.dedent(src))

    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "isinstance"):
            continue
        target = node.args[1]
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Tuple):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    names.add(elt.id)
    return names


def test_every_collector_dispatches_on_every_expression_node():
    members = _expr_union_members()
    for label, method in _COLLECTORS.items():
        missing = sorted(members - _dispatched_names(method))
        assert not missing, (
            f"{label} has no arm for: {missing}.\n"
            "An expression node with no arm gets its nested generic instantiations "
            "silently dropped. Add a recursing arm, or an explicit leaf entry if it "
            "genuinely holds no sub-expression."
        )


def test_no_arm_names_a_node_outside_the_expr_union():
    """The mirror: an arm for a non-Expr node is dead code or a typo."""
    known = _expr_union_members() | {
        # InterpolatedString parts are tested with isinstance(part, str).
        "str",
    }
    for label, method in _COLLECTORS.items():
        stray = sorted(_dispatched_names(method) - known)
        assert not stray, f"{label} dispatches on non-Expr node(s): {stray}"


def test_inert_leaves_have_no_expr_fields():
    """A node treated as inert must have no sub-expression field -- else we skip a subtree."""
    expr_members = _expr_union_members()
    for name in _INERT_LEAF_NAMES:
        node_type = getattr(sushi_ast, name)
        hints = typing.get_type_hints(node_type, globalns=vars(sushi_ast))
        for field, hint in hints.items():
            referenced = {
                t.__name__ for t in typing.get_args(hint) if hasattr(t, "__name__")
            } | ({hint.__name__} if hasattr(hint, "__name__") else set())
            assert not (referenced & expr_members), (
                f"{name} is treated as an inert leaf but its field '{field}' holds an "
                f"expression ({hint}). Give it a recursing arm instead."
            )
