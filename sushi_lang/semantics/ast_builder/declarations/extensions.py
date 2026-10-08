"""Extension method parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import CONVERSION_METHOD, ExtendDef
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
    from sushi_lang.semantics.ast_builder.types.generics import (
        parse_bounded_type_params, parse_extension_target)

    type_params_node = first_tree(suffix.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    params_node = first_tree(suffix.children, "parameters")

    body_node = first_tree(suffix.children, "block") or find_tree_recursive(suffix, "block")
    if body_node is None:
        ice(suffix, "missing body block")

    target_type, target_params = (parse_extension_target(target_type_node, ast_builder)
                                  if target_type_node else (None, ()))
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params)
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
        target_params=target_params,
    )


def parse_handle_extend_stmt_as(t: Tree, ast_builder: 'ASTBuilder') -> ExtendDef:
    """A conversion: `extend <Source> as <Target>:` and a block body.

    It is an `ExtendDef` named `as` (docs/design/error-conversion.md section 8.2). The
    SOURCE is the target type of the extension, and the receiver is `nom self`, not
    written. The TARGET is the return type and also the one method-level type argument,
    so the symbol `extension_symbol(source, "as", (target,))` names each pair. The body
    is bare: the grammar has no place for a `| E`.
    """
    source_node = next((child for child in t.children if is_type_node(child)), None)
    suffix = first_tree(t.children, "extend_as_def")
    if source_node is None or suffix is None:
        ice(t, "missing extend_as_def suffix")

    as_tok = first_token(suffix.children, "AS")
    target_node = next((child for child in suffix.children if is_type_node(child)), None)
    body_node = first_tree(suffix.children, "block")
    if target_node is None or body_node is None:
        ice(suffix, "missing target type or body block")

    # One type and no `| E`, so this is not the signature pair `read_signature_types` reads.
    target = ast_builder._parse_type(target_node)
    body = ast_builder._block(body_node)

    return ExtendDef(
        target_type=ast_builder._parse_type(source_node),
        name=CONVERSION_METHOD,
        params=[],
        ret=target,
        body=body,
        loc=span_of(t),
        target_type_span=span_of(source_node),
        name_span=span_of(as_tok) if as_tok is not None else span_of(t),
        ret_span=span_of(target_node),
        self_mode="nom",
        method_type_args=(target,) if target is not None else None,
        doc=lift_body_doc(body, ast_builder),
    )
