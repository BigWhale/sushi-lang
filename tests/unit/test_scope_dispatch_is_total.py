"""The scope checker must have an arm for every AST node. No silent skips (#245).

`ScopeAnalyzer` has two dispatch sites, and both used to fall through silently:

  - `_check_statement` built a `_check_<typename>` handler name and routed misses to a
    bare `pass` (`_check_unknown_statement`). The `expand(...)` statement lived in that
    gap: its body got no scope analysis at all.
  - `_check_expression` was a `match` with no `case _:` arm. `BlankLit` lived in that
    gap (inert, by luck).

Both sites now raise CE0130 on a miss -- the CE0125 pattern from the borrow checker,
applied to Pass 1. This test is the CI gate; the raise is the runtime backstop. Adding
a node to the AST without teaching the scope checker about it turns CI red instead of
silently disabling scope analysis for it.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.passes.scope import ScopeAnalyzer


def _stmt_subclasses() -> set[str]:
    """Every statement node type: the direct subclasses of `Stmt` (semantics/ast.py)."""
    return {cls.__name__ for cls in sushi_ast.Stmt.__subclasses__()}


def _expr_union_members() -> set[str]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return {t.__name__ for t in typing.get_args(sushi_ast.Expr)}


def _expression_case_names() -> set[str]:
    """Every class name `_check_expression` has a `case X():` arm for."""
    src = inspect.getsource(ScopeAnalyzer._check_expression)
    tree = ast.parse(textwrap.dedent(src))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name):
            names.add(node.cls.id)
    return names


def _expression_has_wildcard_raise() -> bool:
    """`_check_expression` must end in a `case _:` that raises, not passes."""
    src = inspect.getsource(ScopeAnalyzer._check_expression)
    tree = ast.parse(textwrap.dedent(src))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Match):
            continue
        last = node.cases[-1]
        if not isinstance(last.pattern, ast.MatchAs) or last.pattern.pattern is not None:
            return False  # last case is not the wildcard `_`
        body_src = ast.unparse(ast.Module(body=last.body, type_ignores=[]))
        return "raise_internal_error" in body_src
    return False


def test_every_statement_node_has_a_handler():
    """`_check_statement` dispatches by name: `_check_<typename.lower()>` must exist."""
    missing = sorted(
        name for name in _stmt_subclasses()
        if not hasattr(ScopeAnalyzer, f"_check_{name.lower()}")
    )
    assert not missing, (
        f"ScopeAnalyzer has no _check_* handler for statement(s): {missing}.\n"
        "A statement with no handler gets NO scope analysis -- the dispatch raises "
        "CE0130 on it now, so a program containing one is an ICE. Add the handler."
    )


def test_statement_fallthrough_raises():
    """The dispatch miss must raise CE0130, never pass silently."""
    src = inspect.getsource(ScopeAnalyzer._check_statement)
    assert "raise_internal_error" in src and "CE0130" in src, (
        "_check_statement's miss arm must raise CE0130 (the #245 backstop)."
    )
    assert not hasattr(ScopeAnalyzer, "_check_unknown_statement"), (
        "_check_unknown_statement (the silent sink) must stay deleted."
    )


def test_every_expression_node_has_a_case():
    missing = sorted(_expr_union_members() - _expression_case_names())
    assert not missing, (
        f"ScopeAnalyzer._check_expression has no case for: {missing}.\n"
        "An expression node with no case gets NO usage tracking -- the wildcard raises "
        "CE0130 on it now, so a program containing one is an ICE. Add a case (a bare "
        "`pass` case with a comment, if the node is a true leaf)."
    )


def test_no_case_names_a_node_outside_the_expr_union():
    """The mirror: a case for a node that is not an Expr is dead code or a typo."""
    stray = sorted(_expression_case_names() - _expr_union_members())
    assert not stray, f"_check_expression matches non-Expr node(s): {stray}"


def test_expression_wildcard_raises():
    assert _expression_has_wildcard_raise(), (
        "_check_expression must end in a `case _:` that raises CE0130 -- a match with "
        "no wildcard silently skips any node added to the Expr union (#245)."
    )
