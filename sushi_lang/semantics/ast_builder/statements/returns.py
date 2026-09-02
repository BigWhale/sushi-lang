"""Return statement parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Return
from sushi_lang.semantics.ast_builder.utils.expression_discovery import find_outer_expr_structural
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_token, ice, mark_nom
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_return_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Return:
    """Parse return_stmt: RETURN NOM? expr"""
    expr_node = find_outer_expr_structural(node)
    if expr_node is None:
        ice(node, "missing expression")
    value = ast_builder._expr(expr_node)
    nom = first_token(node.children, "NOM")
    if nom is not None:
        mark_nom(value, nom)
    return Return(value=value, loc=span_of(node))
