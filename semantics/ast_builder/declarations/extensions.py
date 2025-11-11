"""Extension method parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Tree
from semantics.ast import ExtendDef
from semantics.typesys import TYPE_NODE_NAMES
from semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_extenddef(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Parse extend_def: EXTEND type NAME "(" [parameters] ")" type ":" block"""
    # Extract target type (first type node)
    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    # Extract method name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("extend_def: missing method NAME")

    # Extract parameters (excluding implicit self)
    params_node = first_tree(t.children, "parameters")

    # Extract return type (second type node)
    return_type_node = None
    type_nodes = [child for child in t.children
                 if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t")]
    if len(type_nodes) >= 2:
        return_type_node = type_nodes[1]
    elif len(type_nodes) == 1:
        # Only one type node found, this might be malformed
        raise NotImplementedError("extend_def: missing return type")

    # Extract body
    body_node = first_tree(t.children, "block") or find_tree_recursive(t, "block")
    if body_node is None:
        raise NotImplementedError("extend_def: missing body block")

    # Parse components
    from semantics.ast_builder.declarations.functions import parse_params
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
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
    )


def parse_handle_extend_stmt_def(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Handle extend_stmt when it's an extension method definition."""
    # extend_stmt: EXTEND type extend_suffix
    # extract_suffix (aliased as extend_def): NAME "(" [parameters] ")" type ":" block

    # Extract target type (first child after EXTEND)
    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    # Find the extend_def suffix
    suffix = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extend_def":
            suffix = child
            break

    if not suffix:
        raise NotImplementedError("extend_stmt: missing extend_def suffix")

    # Extract method name from suffix
    name_tok = first_name(suffix.children)
    if name_tok is None:
        raise NotImplementedError("extend_stmt (def): missing method NAME")

    # Extract parameters
    params_node = first_tree(suffix.children, "parameters")

    # Extract return type (will be a type node in suffix)
    return_type_node = None
    for child in suffix.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            return_type_node = child
            break

    # Extract body
    body_node = first_tree(suffix.children, "block") or find_tree_recursive(suffix, "block")
    if body_node is None:
        raise NotImplementedError("extend_stmt (def): missing body block")

    # Parse components
    from semantics.ast_builder.declarations.functions import parse_params
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
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
    )
