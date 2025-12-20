"""Variable statement parsing (let, rebind)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from lark import Tree
from sushi_lang.semantics.ast import Let, Rebind
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from sushi_lang.semantics.ast_builder.utils.expression_discovery import find_outer_expr_structural, _EXPR_NODES
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_let_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Let:
    """Parse let_stmt: LET [type] NAME "=" expr"""
    nm = first_name(node.children)
    if nm is None:
        raise NotImplementedError("let: NAME missing")

    # Extract type annotation
    type_node = None
    for child in node.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            type_node = child
            break

    ty = ast_builder._parse_type(type_node) if type_node else None
    expr_node = find_outer_expr_structural(node)
    if expr_node is None:
        raise NotImplementedError("let: expression missing")

    return Let(
        name=str(nm),
        ty=ty,
        value=ast_builder._expr(expr_node),
        name_span=span_of(nm),
        type_span=span_of(type_node) if type_node else None,
        loc=span_of(node)
    )


def parse_rebind_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Rebind:
    """Parse rebind_stmt: postfix ":=" expr"""
    # Find the target (postfix expression - can be Name or MemberAccess)
    target_node = first_tree(node.children, "maybe_call")
    if target_node is None:
        raise NotImplementedError("rebind: target missing")

    # Find the value expression (the second expression child, after the target)
    # The grammar is: rebind_stmt: postfix ASSIGN_REBIND expr
    # So we need to find the expr child (which will be or_expr, and_expr, etc.)
    expr_node = None
    for child in node.children:
        if isinstance(child, Tree) and child.data in _EXPR_NODES and child is not target_node:
            expr_node = child
            break

    if expr_node is None:
        raise NotImplementedError("rebind: value expression missing")

    return Rebind(target=ast_builder._expr(target_node), value=ast_builder._expr(expr_node), loc=span_of(node))
