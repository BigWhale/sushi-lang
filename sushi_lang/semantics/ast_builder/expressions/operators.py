"""Operator expression parsing (unary, binary, casts, borrows)."""
from __future__ import annotations
from typing import TYPE_CHECKING, cast
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr, UnaryOp, BinaryOp, CastExpr, Borrow, UnOp, normalize_bin_op
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree_child
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


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
    """Handle borrow expression: &peek expr or &poke expr."""
    from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree_child

    # Extract borrow mode (peek or poke)
    mutability = None
    for child in t.children:
        if isinstance(child, Token) and child.type == "BORROW_MODE":
            mutability = child.value.lower()  # "peek" or "poke"
            break

    if mutability is None:
        # This should not happen with the new grammar
        raise ValueError("Borrow expression missing borrow mode (peek/poke)")

    sub = first_tree_child(t)
    borrowed_expr = ast_builder._expr(sub)
    return Borrow(expr=borrowed_expr, mutability=mutability, loc=span_of(t))

def parse_range_expr(t: Tree, ast_builder: 'ASTBuilder') -> Expr:
    """Parse range expression: start..end or start..=end

    Grammar: range: shift ((RANGE | RANGE_INCLUSIVE) shift)?

    Args:
        t: The range expression tree node
        ast_builder: The AST builder instance

    Returns:
        RangeExpr if range operator present, otherwise the shift expression
    """
    from sushi_lang.semantics.ast import RangeExpr

    children = t.children

    # Simple case: no range operator (just shift expression)
    if len(children) == 1:
        return ast_builder._expr(children[0])

    # Range case: start..end or start..=end
    if len(children) == 3:
        start_expr = ast_builder._expr(children[0])
        operator = children[1]  # Token (RANGE or RANGE_INCLUSIVE)
        end_expr = ast_builder._expr(children[2])

        # Determine if inclusive based on token type
        inclusive = (operator.type == "RANGE_INCLUSIVE")

        return RangeExpr(
            start=start_expr,
            end=end_expr,
            inclusive=inclusive,
            loc=span_of(t)
        )

    # Unexpected structure
    raise NotImplementedError(f"unexpected range node structure: {len(children)} children")
