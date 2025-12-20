"""Return statement parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Return
from sushi_lang.semantics.ast_builder.utils.expression_discovery import find_outer_expr_structural
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_return_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Return:
    """Parse return_stmt: RETURN expr"""
    expr_node = find_outer_expr_structural(node)
    if expr_node is None:
        raise NotImplementedError("return: missing expression")
    return Return(value=ast_builder._expr(expr_node), loc=span_of(node))
