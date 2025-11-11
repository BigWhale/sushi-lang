"""I/O statement parsing (print, println)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from semantics.ast import Print, PrintLn
from semantics.ast_builder.utils.expression_discovery import find_outer_expr_structural
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_print_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Print:
    """Parse print_stmt: PRINT expr"""
    expr_node = find_outer_expr_structural(node)
    if expr_node is None:
        raise NotImplementedError("print: missing expression")
    return Print(value=ast_builder._expr(expr_node), loc=span_of(node))


def parse_println_stmt(node: Tree, ast_builder: 'ASTBuilder') -> PrintLn:
    """Parse println_stmt: PRINTLN expr"""
    expr_node = find_outer_expr_structural(node)
    if expr_node is None:
        raise NotImplementedError("println: missing expression")
    return PrintLn(value=ast_builder._expr(expr_node), loc=span_of(node))
