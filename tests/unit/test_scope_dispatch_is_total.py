"""The scope checker must have an arm for every AST node. No silent skips (#245, #686).

Statements dispatch through `scope._STATEMENT_HANDLERS`, a table keyed on the node CLASS;
expressions dispatch with `match`. Both end in a located CE0130 backstop. Until #686 the
statement side built a method name from the lowercased class name and asked `hasattr`, so
a class RENAME moved every statement of that kind to the backstop in silence and a
lowercased-name COLLISION ran the wrong arm with no signal -- the shape #639 closed in
`semantics/visitors.py`. The gate below is that gate's, read against this table.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import typing

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.passes.scope import _STATEMENT_HANDLERS, ScopeAnalyzer


def _stmt_subclasses() -> set[type]:
    """Every statement node type: the direct subclasses of `Stmt` (semantics/ast.py)."""
    return set(sushi_ast.Stmt.__subclasses__())


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


def _block() -> sushi_ast.Block:
    return sushi_ast.Block(None, [sushi_ast.ExprStmt(None, sushi_ast.IntLit(None, 0))])


def _every_statement() -> dict[type, sushi_ast.Stmt]:
    """One value per statement kind, each with its real children filled in."""
    a = sushi_ast
    return {
        a.Let: a.Let(None, "x", None, a.IntLit(None, 0)),
        a.Rebind: a.Rebind(None, a.Name(None, "x"), a.IntLit(None, 1)),
        a.ExprStmt: a.ExprStmt(None, a.Name(None, "x")),
        a.Return: a.Return(None, a.IntLit(None, 0)),
        a.Print: a.Print(None, a.StringLit(None, "s")),
        a.PrintLn: a.PrintLn(None, a.StringLit(None, "s")),
        a.If: a.If(None, [(a.BoolLit(None, True), _block())], _block()),
        a.While: a.While(None, a.BoolLit(None, True), _block()),
        a.Foreach: a.Foreach(None, "i", None, a.Name(None, "xs"), _block()),
        a.Expand: a.Expand(None, "p", a.Name(None, "args"), _block()),
        a.Match: a.Match(None, a.Name(None, "r"), [
            a.MatchArm(None, a.WildcardPattern(None), _block()),
        ]),
        a.Break: a.Break(None),
        a.Continue: a.Continue(None),
    }


def _analyzer() -> ScopeAnalyzer:
    from sushi_lang.internals.report import Reporter

    checker = ScopeAnalyzer(Reporter(source="", filename="<gate>"))
    checker._push_scope()
    return checker


# --- The table, in both directions.

def test_every_statement_node_has_a_row():
    missing = sorted(cls.__name__ for cls in _stmt_subclasses()
                     if cls not in _STATEMENT_HANDLERS)
    assert not missing, (
        f"_STATEMENT_HANDLERS has no row for statement(s): {missing}.\n"
        "A statement with no row gets NO scope analysis -- the dispatch raises CE0130 "
        "on it now, so a program containing one is an ICE. Add the row and its arm."
    )


def test_no_row_names_a_kind_the_dispatch_cannot_reach():
    """The mirror: a row for a node that is never a statement is dead code or a typo."""
    stray = sorted(cls.__name__ for cls in _STATEMENT_HANDLERS
                   if cls not in _stmt_subclasses())
    assert not stray, (
        f"_STATEMENT_HANDLERS names non-statement node(s): {stray}. `_check_block` hands "
        "the dispatch a `Block.statements` member and nothing else."
    )


def test_every_row_holds_a_real_method_of_the_analyzer():
    """A row is an unbound method, so a rename is caught at import; check it is ours."""
    stray = sorted(cls.__name__ for cls, fn in _STATEMENT_HANDLERS.items()
                   if getattr(ScopeAnalyzer, fn.__name__, None) is not fn)
    assert not stray, (
        f"_STATEMENT_HANDLERS rows that are not a ScopeAnalyzer method: {stray}."
    )


def test_the_dispatch_reads_the_table_and_not_a_built_name():
    """What #686 closed: no `hasattr`/`getattr` on a name built from the class name."""
    src = inspect.getsource(ScopeAnalyzer._check_statement)
    assert "_STATEMENT_HANDLERS" in src, (
        "_check_statement must dispatch through the table."
    )
    assert "hasattr" not in src and "getattr(self" not in src, (
        "_check_statement must not build a handler NAME from the class name: a rename "
        "then moves every statement of that kind to the backstop in silence (#686)."
    )


# --- Behaviour, not source reading.

def test_every_statement_kind_has_a_fixture():
    """The behaviour tests below prove nothing unless every kind is exercised."""
    uncovered = sorted(cls.__name__ for cls in _stmt_subclasses()
                       if cls not in _every_statement())
    assert not uncovered, f"statement kinds with no fixture: {uncovered}"


@pytest.mark.parametrize("kind", sorted(_every_statement(), key=lambda c: c.__name__))
def test_no_legal_statement_reaches_the_backstop(kind):
    """Every statement kind walks without an ICE."""
    _analyzer()._check_statement(_every_statement()[kind])


def test_the_statement_backstop_raises():
    """A node that is not a statement reaches CE0130, it is not skipped in silence."""
    stray = sushi_ast.ArrayElement(Span(4, 7, 4, 9), sushi_ast.IntLit(None, 0))
    with pytest.raises(InternalCompilerError) as caught:
        _analyzer()._check_statement(stray)  # type: ignore[arg-type]
    assert caught.value.code == "CE0130"
    assert caught.value.params.get("node") == "ArrayElement"


def test_the_statement_backstop_is_located():
    """A diagnostic with no span cannot point at the source that provoked it."""
    span = Span(11, 2, 11, 8)
    with pytest.raises(InternalCompilerError) as caught:
        _analyzer()._check_statement(
            sushi_ast.ArrayElement(span, sushi_ast.IntLit(None, 0)))  # type: ignore[arg-type]
    assert caught.value.span == span


def test_the_expression_backstop_is_located():
    span = Span(3, 1, 3, 6)
    with pytest.raises(InternalCompilerError) as caught:
        _analyzer()._check_expression(
            sushi_ast.ArrayElement(span, sushi_ast.IntLit(None, 0)))  # type: ignore[arg-type]
    assert caught.value.code == "CE0130"
    assert caught.value.span == span


# --- The expression side, unchanged in shape since #245.

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


def test_the_silent_sink_stays_deleted():
    assert not hasattr(ScopeAnalyzer, "_check_unknown_statement"), (
        "_check_unknown_statement (the silent sink) must stay deleted."
    )
