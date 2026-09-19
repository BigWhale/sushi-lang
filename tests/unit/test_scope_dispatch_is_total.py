"""The scope checker must have an arm for every AST node. No silent skips (#245, #686).

Statements dispatch through `scope._STATEMENT_HANDLERS` and expressions through
`scope._EXPRESSION_HANDLERS`, each a table keyed on the node CLASS, and each ends in a
located CE0130 backstop. Until #686 the statement side built a method name from the
lowercased class name and asked `hasattr`, so a class RENAME moved every statement of
that kind to the backstop in silence and a lowercased-name COLLISION ran the wrong arm
with no signal -- the shape #639 closed in `semantics/visitors.py`. The expression side
was a 24-arm `match` whose gate read the SOURCE of the method; it reads the table now,
so both halves answer the same question the same way.
"""
from __future__ import annotations

import gc
import inspect
import sys
import typing
from dataclasses import dataclass

import pytest

from sushi_lang.internals.diagnostics import InternalCompilerError
from sushi_lang.internals.report import Span
from sushi_lang.semantics import ast as sushi_ast
from sushi_lang.semantics.typesys import BuiltinType
from sushi_lang.semantics.passes.scope import (
    _EXPRESSION_HANDLERS, _LEAF_EXPRS, _STATEMENT_HANDLERS, ScopeAnalyzer)


def _stmt_subclasses() -> set[type]:
    """Every statement node type: the DECLARED subclasses of `Stmt` (semantics/ast.py).

    `Stmt.__subclasses__()` answers more than that (#735). Each node is declared with
    `@dataclass(slots=True)`, and that decorator cannot add `__slots__` to a class that
    is already built: it builds a SECOND class and drops the first. The dropped class is
    already registered as a subclass, and it is reachable only through a reference cycle,
    so only the cyclic collector can free it. With `gc.disable()` all thirteen statement
    kinds are in the list twice. With the collector on, whether one is still there when
    this file runs depends on how much the process has allocated, which is why these
    gates failed on a run of this file alone and passed in the full suite.

    A declared class is the attribute that its own module holds under its name. A
    dropped class never is, because the decorator gave the module the replacement.
    """
    return {cls for cls in sushi_ast.Stmt.__subclasses__()
            if getattr(sys.modules.get(cls.__module__), cls.__name__, None) is cls}


def _expr_union_types() -> set[type]:
    """Every node type in the `Expr` union (semantics/ast.py)."""
    return set(typing.get_args(sushi_ast.Expr))


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


def _every_expression() -> dict[type, sushi_ast.Expr]:
    """One value per expression kind, each with its real children filled in."""
    a = sushi_ast
    return {
        a.Name: a.Name(None, "x"),
        a.IntLit: a.IntLit(None, 0),
        a.FloatLit: a.FloatLit(None, 1.5),
        a.BoolLit: a.BoolLit(None, True),
        a.StringLit: a.StringLit(None, "s"),
        a.BlankLit: a.BlankLit(None),
        a.InterpolatedString: a.InterpolatedString(None, ["n=", a.Name(None, "x")]),
        a.ArrayLiteral: a.ArrayLiteral(None, [
            a.ArrayElement(None, a.Name(None, "x"), a.Name(None, "n")),
        ]),
        a.IndexAccess: a.IndexAccess(None, a.Name(None, "xs"), a.Name(None, "i")),
        a.UnaryOp: a.UnaryOp(None, "-", a.Name(None, "x")),
        a.BinaryOp: a.BinaryOp(None, "+", a.Name(None, "x"), a.Name(None, "y")),
        a.Call: a.Call(None, a.Name(None, "f"), [a.Name(None, "x")]),
        a.MethodCall: a.MethodCall(None, a.Name(None, "r"), "m", [a.Name(None, "x")]),
        a.DotCall: a.DotCall(None, a.Name(None, "r"), "m", [a.Name(None, "x")]),
        a.MemberAccess: a.MemberAccess(None, a.Name(None, "r"), "f"),
        a.EnumConstructor: a.EnumConstructor(None, "Color", "Red", [a.Name(None, "x")]),
        a.DynamicArrayNew: a.DynamicArrayNew(None),
        a.DynamicArrayFrom: a.DynamicArrayFrom(None, a.ArrayLiteral(None, [
            a.ArrayElement(None, a.Name(None, "x")),
        ])),
        a.CastExpr: a.CastExpr(None, a.Name(None, "x"), BuiltinType.I64),
        a.Borrow: a.Borrow(None, a.Name(None, "x"), "peek"),
        a.TryExpr: a.TryExpr(None, a.Name(None, "x")),
        a.RangeExpr: a.RangeExpr(None, a.Name(None, "lo"), a.Name(None, "hi"), False),
        a.Spread: a.Spread(None, a.Name(None, "xs")),
        a.Lambda: a.Lambda(None, [a.Param("p", None)], a.Name(None, "p")),
    }


def _analyzer() -> ScopeAnalyzer:
    from sushi_lang.internals.report import Reporter

    checker = ScopeAnalyzer(Reporter(source="", filename="<gate>"))
    checker._push_scope()
    return checker


# --- The table, in both directions.

def test_the_statement_set_holds_only_the_declared_classes():
    """`Stmt.__subclasses__()` is not the declared set (#735).

    `semantics/ast.py` declares every node with `@dataclass(slots=True)`. That decorator
    cannot add `__slots__` to a class that is already built, so it builds a SECOND class
    and drops the first. The dropped class is already registered as a subclass of `Stmt`,
    and it is reachable only through a reference cycle, so it stays in the subclass list
    until the cyclic collector frees it.

    The stand-in below has that same shape: a class that carries the name of a declared
    statement, on an object that is not the declaration. `_stmt_subclasses()` must not
    answer it. If it does, the gates below compare the table against a name that is
    already in it, and they report a fault that does not exist.
    """
    before = set(sushi_ast.Stmt.__subclasses__())

    @dataclass(slots=True)
    class Continue(sushi_ast.Stmt):
        pass

    try:
        added = set(sushi_ast.Stmt.__subclasses__()) - before
        assert added, (
            "the stand-in did not register as a subclass of `Stmt`; the shape this gate "
            "guards has changed, so read the test before you change the code."
        )
        leaked = sorted(cls.__name__ for cls in added & _stmt_subclasses())
        assert not leaked, (
            f"_stmt_subclasses() answers a class that no module holds under its own "
            f"name: {leaked}.\n"
            "Read the DECLARED classes, not `Stmt.__subclasses__()`."
        )
    finally:
        del Continue
        gc.collect()


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
    assert not uncovered, (
        f"`_every_statement()` has no value for statement kind(s): {uncovered}.\n"
        "A kind with no value is walked by none of the behaviour tests below, so "
        "nothing here says its arm runs. Add the kind, with its real children filled "
        "in.\n"
        "A name that `_every_statement()` clearly holds means that two different "
        "classes carry it. Read `_stmt_subclasses()` first: the set is keyed on the "
        "class OBJECT, not on the name."
    )


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


# --- The expression table, the same four questions the statement table answers.

def test_every_expression_node_has_a_row():
    missing = sorted(cls.__name__ for cls in _expr_union_types()
                     if cls not in _EXPRESSION_HANDLERS)
    assert not missing, (
        f"_EXPRESSION_HANDLERS has no row for: {missing}.\n"
        "An expression node with no row gets NO usage tracking -- the dispatch raises "
        "CE0130 on it now, so a program containing one is an ICE. Add the row and its "
        "arm (`_LEAF_EXPRS`, if the node is a true leaf)."
    )


def test_no_expression_row_names_a_kind_outside_the_expr_union():
    """The mirror: a row for a node that is not an Expr is dead code or a typo."""
    stray = sorted(cls.__name__ for cls in _EXPRESSION_HANDLERS
                   if cls not in _expr_union_types())
    assert not stray, f"_EXPRESSION_HANDLERS names non-Expr node(s): {stray}"


def test_every_expression_row_holds_a_real_method_of_the_analyzer():
    """A row is an unbound method, so a rename is caught at import; check it is ours."""
    stray = sorted(cls.__name__ for cls, fn in _EXPRESSION_HANDLERS.items()
                   if getattr(ScopeAnalyzer, fn.__name__, None) is not fn)
    assert not stray, (
        f"_EXPRESSION_HANDLERS rows that are not a ScopeAnalyzer method: {stray}."
    )


def test_the_expression_dispatch_reads_the_table_and_not_a_built_name():
    src = inspect.getsource(ScopeAnalyzer._check_expression)
    assert "_EXPRESSION_HANDLERS" in src, (
        "_check_expression must dispatch through the table."
    )
    assert "hasattr" not in src and "getattr(self" not in src, (
        "_check_expression must not build a handler NAME from the class name: a rename "
        "then moves every expression of that kind to the backstop in silence (#686)."
    )


def test_every_leaf_row_points_at_the_leaf_arm():
    """`_LEAF_EXPRS` is what the table calls a leaf; it must say so through one arm."""
    stray = sorted(cls.__name__ for cls in _LEAF_EXPRS
                   if _EXPRESSION_HANDLERS.get(cls)
                   is not ScopeAnalyzer._check_leaf_expression)
    assert not stray, f"_LEAF_EXPRS members with another arm: {stray}"


def test_the_leaf_rows_really_are_leaves():
    """A 'leaf' with an expression field is a subtree this walk skips (#245)."""
    expr_types = _expr_union_types()
    for node_type in _LEAF_EXPRS:
        hints = typing.get_type_hints(node_type, globalns=vars(sushi_ast))
        for field, hint in hints.items():
            referenced = {t for t in typing.get_args(hint)} | {hint}
            assert not (referenced & expr_types), (
                f"{node_type.__name__} is in _LEAF_EXPRS but its field '{field}' holds "
                f"an expression ({hint}). It is not a leaf -- give it a real arm."
            )


def test_every_expression_kind_has_a_fixture():
    """The behaviour tests below prove nothing unless every kind is exercised."""
    uncovered = sorted(cls.__name__ for cls in _expr_union_types()
                       if cls not in _every_expression())
    assert not uncovered, f"expression kinds with no fixture: {uncovered}"


@pytest.mark.parametrize("kind", sorted(_every_expression(), key=lambda c: c.__name__))
def test_no_legal_expression_reaches_the_backstop(kind):
    """Every expression kind walks without an ICE."""
    _analyzer()._check_expression(_every_expression()[kind])


def test_the_expression_backstop_raises():
    """A node that is not an expression reaches CE0130, it is not skipped in silence."""
    stray = sushi_ast.ArrayElement(Span(4, 7, 4, 9), sushi_ast.IntLit(None, 0))
    with pytest.raises(InternalCompilerError) as caught:
        _analyzer()._check_expression(stray)  # type: ignore[arg-type]
    assert caught.value.code == "CE0130"
    assert caught.value.params.get("node") == "ArrayElement"


def test_the_silent_sink_stays_deleted():
    assert not hasattr(ScopeAnalyzer, "_check_unknown_statement"), (
        "_check_unknown_statement (the silent sink) must stay deleted."
    )
