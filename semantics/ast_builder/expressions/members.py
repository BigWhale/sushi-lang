"""Member access and index access parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from semantics.ast import Expr, MemberAccess, IndexAccess
from semantics.ast_builder.utils.tree_navigation import first_tree, first_method_name
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def member_access_from_parts(receiver: Expr, member_access_node: Tree) -> MemberAccess:
    """Parse member_access: \".\" NAME"""
    assert member_access_node.data == "member_access"

    method_name_tree = first_tree(member_access_node.children, "method_name")
    if method_name_tree:
        member_name_tok = first_method_name(method_name_tree.children)
    else:
        member_name_tok = first_method_name(member_access_node.children)

    if member_name_tok is None:
        raise NotImplementedError("member_access: missing member NAME")

    return MemberAccess(
        receiver=receiver,
        member=str(member_name_tok),
        loc=span_of(member_access_node)
    )


def index_access_from_parts(array_expr: Expr, index_node: Tree, ast_builder: 'ASTBuilder') -> IndexAccess:
    """Parse index: \"[\" expr \"]\" """
    assert index_node.data == "index"

    index_expr_node = None
    for child in index_node.children:
        if isinstance(child, Tree):
            index_expr_node = child
            break

    if index_expr_node is None:
        raise NotImplementedError("index: missing index expression")

    return IndexAccess(
        array=array_expr,
        index=ast_builder._expr(index_expr_node),
        loc=span_of(index_node)
    )
