"""I/O statement parsing (print, println, assert)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Assert, Print, PrintLn
from sushi_lang.semantics.ast_builder.utils.expression_discovery import EXPR_NODES, statement_expr
from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_print_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Print:
    """Parse print_stmt: PRINT expr"""
    expr_node = statement_expr(node)
    if expr_node is None:
        ice(node, "missing expression")
    return Print(value=ast_builder._expr(expr_node), loc=span_of(node))


def parse_println_stmt(node: Tree, ast_builder: 'ASTBuilder') -> PrintLn:
    """Parse println_stmt: PRINTLN expr"""
    expr_node = statement_expr(node)
    if expr_node is None:
        ice(node, "missing expression")
    return PrintLn(value=ast_builder._expr(expr_node), loc=span_of(node))


def parse_assert_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Assert:
    """Parse assert_stmt: ASSERT "(" expr ["," expr] ")" -- the condition, then the message."""
    exprs = [c for c in node.children if isinstance(c, Tree) and c.data in EXPR_NODES]
    if not exprs or len(exprs) > 2:
        ice(node, f"expected a condition and at most one message, got {len(exprs)}")
    message = ast_builder._expr(exprs[1]) if len(exprs) == 2 else None
    return Assert(cond=ast_builder._expr(exprs[0]), message=message,
                  source_label=ast_builder.source_label, loc=span_of(node))
