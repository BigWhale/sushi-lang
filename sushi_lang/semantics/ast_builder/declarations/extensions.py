"""Extension method parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.ast_builder.declarations.docs import lift_body_doc
from sushi_lang.semantics.ast_builder.declarations.signatures import read_signature_types
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    find_tree_recursive, first_token, first_tree, ice, is_type_node,
    read_method_name)
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_handle_extend_stmt_def(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Handle extend_stmt when it's an extension method definition.

    Suffix shape: NAME [type_params] "(" [parameters] ")" type ["|" type] ":" block.
    The TARGET type stands on the outer node and the signature on the suffix, so the
    two reads take the children of different nodes.
    """

    target_type_node = None
    for child in t.children:
        if is_type_node(child):
            target_type_node = child
            break

    suffix = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extend_def":
            suffix = child
            break

    if not suffix:
        ice(t, "missing extend_def suffix")

    name_tok = read_method_name(suffix)

    static_tok = first_token(suffix.children, "STATIC")

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params

    type_params_node = first_tree(suffix.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(suffix.children, "parameters")

    body_node = first_tree(suffix.children, "block") or find_tree_recursive(suffix, "block")
    if body_node is None:
        ice(suffix, "missing body block")

    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))
    signature = read_signature_types(suffix.children, ast_builder)
    body = ast_builder._block(body_node)

    return ExtendDef(
        target_type=target_type,
        name=str(name_tok),
        params=params,
        ret=signature.ret,
        body=body,
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        name_span=span_of(name_tok),
        ret_span=signature.ret_span,
        self_mode=self_mode,
        self_mode_span=self_mode_span,
        type_params=type_params,
        err_type=signature.err,
        err_span=signature.err_span,
        doc=lift_body_doc(body, ast_builder),
        is_static=static_tok is not None,
        static_span=span_of(static_tok) if static_tok is not None else None,
    )
