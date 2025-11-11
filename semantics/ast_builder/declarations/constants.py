"""Constant definition parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from semantics.ast import ConstDef
from semantics.typesys import TYPE_NODE_NAMES
from semantics.ast_builder.utils.tree_navigation import first_name
from semantics.ast_builder.utils.expression_discovery import _EXPR_NODES
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_constdef(t: Tree, ast_builder: 'ASTBuilder') -> ConstDef:
    """Parse const_def: CONST type NAME "=" expr"""
    # Constants are always global by design

    # Extract constant type (first type node)
    type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            type_node = child
            break

    # Extract constant name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("const_def: missing constant NAME")

    # Extract value expression
    value_expr = None
    for child in t.children:
        if isinstance(child, Tree) and child.data in _EXPR_NODES:
            value_expr = child
            break

    if value_expr is None:
        raise NotImplementedError("const_def: missing value expression")

    # Parse components
    const_type = ast_builder._parse_type(type_node) if type_node else None
    value = ast_builder._expr(value_expr)

    return ConstDef(
        name=str(name_tok),
        ty=const_type,
        value=value,
        is_public=True,  # Constants are always global
        loc=span_of(t),
        name_span=span_of(name_tok),
        type_span=span_of(type_node),
    )
