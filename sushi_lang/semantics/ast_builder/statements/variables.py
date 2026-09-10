"""Variable statement parsing (let, rebind)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Let, Rebind
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_name, first_token, first_tree, ice, is_type_node, mark_nom)
from sushi_lang.semantics.ast_builder.utils.expression_discovery import EXPR_NODES, statement_expr
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_let_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Let:
    """Parse let_stmt: LET [type] NAME "=" NOM? expr"""
    nm = first_name(node.children)
    if nm is None:
        ice(node, "NAME missing")

    type_node = None
    for child in node.children:
        if is_type_node(child):
            type_node = child
            break

    ty = ast_builder._parse_type(type_node) if type_node else None

    lambda_block_node = first_tree(node.children, "lambda_block")
    if lambda_block_node is not None:
        from sushi_lang.semantics.ast_builder.expressions import lambdas
        value = lambdas.parse_lambda(lambda_block_node, ast_builder)
    else:
        expr_node = statement_expr(node)
        if expr_node is None:
            ice(node, "expression missing")
        value = ast_builder._expr(expr_node)
        nom = first_token(node.children, "NOM")
        if nom is not None:
            mark_nom(value, nom)

    return Let(
        name=str(nm),
        ty=ty,
        value=value,
        name_span=span_of(nm),
        type_span=span_of(type_node) if type_node else None,
        loc=span_of(node)
    )


def parse_rebind_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Rebind:
    """Parse rebind_stmt: postfix ":=" expr"""
    target_node = first_tree(node.children, "maybe_call")
    if target_node is None:
        ice(node, "target missing")

    # `rebind_stmt: postfix ASSIGN_REBIND expr` holds two expressions, and the
    # target is the first, so the value is the other one.
    expr_node = None
    for child in node.children:
        if isinstance(child, Tree) and child.data in EXPR_NODES and child is not target_node:
            expr_node = child
            break

    if expr_node is None:
        ice(node, "value expression missing")

    return Rebind(target=ast_builder._expr(target_node), value=ast_builder._expr(expr_node), loc=span_of(node))
