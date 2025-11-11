"""Operator expression parsing (unary, binary, casts, borrows)."""
from __future__ import annotations
from typing import TYPE_CHECKING, cast
from lark import Tree, Token
from semantics.ast import Expr, UnaryOp, BinaryOp, CastExpr, Borrow, UnOp, normalize_bin_op
from semantics.ast_builder.utils.tree_navigation import first_tree_child
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def expr_unary(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Handle unary operators: neg, not, bitnot, and fallback unary wrapper."""
    tag = t.data

    if tag in {"neg", "not"}:
        sub = first_tree_child(t)
        op = cast(UnOp, tag)
        rhs = ast_builder._expr(sub)
        return UnaryOp(op=op, expr=rhs, loc=span_of(t))

    if tag == "bitnot":
        sub = first_tree_child(t)
        rhs = ast_builder._expr(sub)
        return UnaryOp(op="~", expr=rhs, loc=span_of(t))

    if tag == "unary":
        if len(t.children) == 2 and isinstance(t.children[0], Token):
            tok: Token = t.children[0]
            sub = first_tree_child(t)
            rhs = ast_builder._expr(sub)
            if tok.type in ("MINUS",) or tok.value == "-":
                return UnaryOp(op="neg", expr=rhs, loc=span_of(t))
            return UnaryOp(op="not", expr=rhs, loc=span_of(t))
        return ast_builder._expr(t.children[0])

    raise NotImplementedError(f"unexpected unary node: {tag}")


def bin_chain(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Lower binary operator chains left-associatively."""
    items: list[Expr | str] = []
    for c in t.children:
        if isinstance(c, Tree):
            items.append(ast_builder._expr(c))
        elif isinstance(c, Token):
            items.append(c.value)

    if not items:
        raise NotImplementedError("empty binary chain")

    lhs = items[0]
    i = 1
    while i + 1 < len(items):
        op_raw = items[i]
        rhs = items[i + 1]
        op = normalize_bin_op(op_raw)
        lhs = BinaryOp(op=op, left=lhs, right=rhs, loc=span_of(t))
        i += 2

    return lhs  # type: ignore[return-value]


def handle_cast(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Handle cast expressions: expr as type (as type)*."""
    children = t.children

    # Simple case: just one expression (no cast)
    if len(children) == 1:
        return ast_builder._expr(children[0])

    # Cast case: expr as type (as type)*
    expr = ast_builder._expr(children[0])

    # Handle multiple casts: (expr as type1) as type2
    i = 1
    while i + 1 < len(children):
        # children[i] should be "as" token, children[i+1] should be type
        if i + 1 >= len(children):
            break
        target_type = ast_builder._parse_type(children[i + 1])
        expr = CastExpr(expr=expr, target_type=target_type, loc=span_of(t))
        i += 2

    return expr


def expr_borrow(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Handle borrow expression: &expr."""
    from semantics.ast_builder.utils.tree_navigation import first_tree_child

    sub = first_tree_child(t)
    borrowed_expr = ast_builder._expr(sub)
    return Borrow(expr=borrowed_expr, loc=span_of(t))
