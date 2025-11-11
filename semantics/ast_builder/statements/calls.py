"""Call statement parsing (expression statement)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from semantics.ast import ExprStmt
from semantics.ast_builder.utils.tree_navigation import first_tree
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_call_stmt(node: Tree, ast_builder: 'ASTBuilder') -> ExprStmt:
    """Parse call_stmt: postfix _NEWLINE"""
    maybe_call_node = first_tree(node.children, "maybe_call")
    if maybe_call_node is None:
        raise NotImplementedError("call_stmt: missing maybe_call")
    call_expr = ast_builder._expr(maybe_call_node)
    return ExprStmt(expr=call_expr, loc=span_of(node))
