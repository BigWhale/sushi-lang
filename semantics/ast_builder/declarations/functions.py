"""Function definition and parameter parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from semantics.ast import FuncDef, Param, BoundedTypeParam
from semantics.typesys import Type, TYPE_NODE_NAMES
from semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_funcdef(t: Tree, ast_builder: 'ASTBuilder') -> FuncDef:
    """Parse function_def: [PUBLIC] FN NAME [type_params] "(" [parameters] ")" type ":" block"""
    name_tok = first_name(t.children)

    if name_tok is None:
        raise NotImplementedError("function_def: missing NAME")

    # Check for PUBLIC token
    is_public = False
    for child in t.children:
        if isinstance(child, Token) and child.type == "PUBLIC":
            is_public = True
            break

    # Extract type parameters if present
    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(t.children, "parameters")
    # Look for any type node (including user-defined types via name_t)
    ret_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            ret_node = child
            break

    body_node = first_tree(t.children, "block") or find_tree_recursive(t, "block")
    if body_node is None:
        raise NotImplementedError("function_def: missing body block")

    params = parse_params(params_node, ast_builder) if params_node else []
    ret_ty: Optional[Type] = ast_builder._parse_type(ret_node) if ret_node is not None else None
    return FuncDef(
        name=str(name_tok),
        params=params,
        ret=ret_ty,
        body=ast_builder._block(body_node),
        is_public=is_public,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
        ret_span=span_of(ret_node),
    )


def parse_params(t: Tree, ast_builder: 'ASTBuilder') -> List[Param]:
    """Parse parameters: typed_param ("," typed_param)*"""
    assert t.data == "parameters"

    out: List[Param] = []
    for ch in t.children:
        if isinstance(ch, Tree) and ch.data == "typed_param":
            ty_node = next(
                (
                    sub
                    for sub in ch.children
                    if isinstance(sub, Tree)
                    and (sub.data in TYPE_NODE_NAMES or sub.data == "name_t")
                ),
                None,
            )
            if ty_node is None:
                raise NotImplementedError("typed_param: missing type")

            ty = ast_builder._parse_type(ty_node)

            nm_tok = first_name(ch.children)
            if nm_tok is None:
                raise NotImplementedError("typed_param: missing NAME")

            out.append(
                Param(
                    name=str(nm_tok),
                    ty=ty,
                    name_span=span_of(nm_tok),
                    type_span=span_of(ty_node),
                    loc=span_of(ch),
                )
            )

    return out


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node and extract bounded type parameters with constraints.

    Delegates to generics module for parsing.
    """
    from semantics.ast_builder.types.generics import parse_bounded_type_params as _parse_bounded
    return _parse_bounded(type_params_node)
