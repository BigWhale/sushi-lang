"""Extension method parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive, ice
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_extenddef(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Parse extend_def: EXTEND type NAME "(" [parameters] ")" type ":" block"""
    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing method NAME")

    params_node = first_tree(t.children, "parameters")

    return_type_node = None
    type_nodes = [child for child in t.children
                 if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t")]
    if len(type_nodes) >= 2:
        return_type_node = type_nodes[1]
    elif len(type_nodes) == 1:
        ice(t, "missing return type")

    body_node = first_tree(t.children, "block") or find_tree_recursive(t, "block")
    if body_node is None:
        ice(t, "missing body block")

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))
    return_type = ast_builder._parse_type(return_type_node) if return_type_node else None

    return ExtendDef(
        target_type=target_type,
        name=str(name_tok),
        params=params,
        ret=return_type,
        body=ast_builder._block(body_node),
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        name_span=span_of(name_tok),
        ret_span=span_of(return_type_node),
        self_mode=self_mode,
        self_mode_span=self_mode_span,
    )


def parse_handle_extend_stmt_def(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Handle extend_stmt when it's an extension method definition."""

    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    suffix = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extend_def":
            suffix = child
            break

    if not suffix:
        ice(t, "missing extend_def suffix")

    name_tok = first_name(suffix.children)
    if name_tok is None:
        ice(suffix, "missing method NAME")

    params_node = first_tree(suffix.children, "parameters")

    return_type_node = None
    for child in suffix.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            return_type_node = child
            break

    body_node = first_tree(suffix.children, "block") or find_tree_recursive(suffix, "block")
    if body_node is None:
        ice(suffix, "missing body block")

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))
    return_type = ast_builder._parse_type(return_type_node) if return_type_node else None

    return ExtendDef(
        target_type=target_type,
        name=str(name_tok),
        params=params,
        ret=return_type,
        body=ast_builder._block(body_node),
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        name_span=span_of(name_tok),
        ret_span=span_of(return_type_node),
        self_mode=self_mode,
        self_mode_span=self_mode_span,
    )
