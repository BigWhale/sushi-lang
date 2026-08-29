"""Extension method parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import ExtendDef
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.declarations.docs import lift_body_doc
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree, find_tree_recursive, ice
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_handle_extend_stmt_def(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """Handle extend_stmt when it's an extension method definition.

    Suffix shape: NAME [type_params] "(" [parameters] ")" type ["|" type] ":" block.
    With `maybe_placeholders=False` an omitted optional emits no child, so the
    return and error types are the first and second type nodes among the suffix's
    DIRECT children (the parameters subtree nests its own type nodes one level down).
    """

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

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params

    type_params_node = first_tree(suffix.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(suffix.children, "parameters")

    type_nodes = [child for child in suffix.children
                  if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t")]
    return_type_node = type_nodes[0] if len(type_nodes) >= 1 else None
    err_type_node = type_nodes[1] if len(type_nodes) >= 2 else None

    body_node = first_tree(suffix.children, "block") or find_tree_recursive(suffix, "block")
    if body_node is None:
        ice(suffix, "missing body block")

    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))
    return_type = ast_builder._parse_type(return_type_node) if return_type_node else None
    err_type = ast_builder._parse_type(err_type_node) if err_type_node else None
    body = ast_builder._block(body_node)

    return ExtendDef(
        target_type=target_type,
        name=str(name_tok),
        params=params,
        ret=return_type,
        body=body,
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        name_span=span_of(name_tok),
        ret_span=span_of(return_type_node),
        self_mode=self_mode,
        self_mode_span=self_mode_span,
        type_params=type_params,
        err_type=err_type,
        err_span=span_of(err_type_node) if err_type_node is not None else None,
        doc=lift_body_doc(body, ast_builder),
    )
