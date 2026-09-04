"""Perk definition and implementation parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import PerkDef, PerkMethodSignature, ExtendWithDef, FuncDef
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    expect, first_name, first_tree, ice, read_public)
from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.visibility import declared_public

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_impl_methods(children: List, ast_builder: 'ASTBuilder') -> List[FuncDef]:
    """Build an implementation's methods, and refuse a marker on one (CE6103).

    An implementation body is made of `function_def`, so `public` parses here and used to
    be stored on the method where nothing read it. Ruling 2 says an implementation has no
    marker of its own.
    """
    from sushi_lang.semantics.ast_builder.declarations.functions import parse_funcdef

    methods: List[FuncDef] = []
    # A `static` marker rides the block, not the shared `function_def`, so it arrives
    # as a SIBLING token standing just before the method it was written on. Pairing it
    # here is the only place source order still says which method that is; the refusal
    # itself is the perk pass's (CE4014, ruling R1).
    pending_static: Optional[Token] = None
    for child in children:
        if isinstance(child, Token) and child.type == "STATIC":
            pending_static = child
            continue
        if isinstance(child, Tree) and child.data == "function_def":
            method = parse_funcdef(child, ast_builder)
            if method.public_span is not None:
                raise SyntaxDiagnostic("CE6103", span=method.public_span) \
                    .help("an implementation is as visible as its target type; "
                          "mark the type instead")
            if pending_static is not None:
                method.static_span = span_of(pending_static)
                pending_static = None
            methods.append(method)
    return methods


def parse_perkdef(t: Tree, ast_builder: 'ASTBuilder') -> PerkDef:
    """Parse perk_def: PERK NAME [type_params] ":" _NEWLINE _INDENT perk_method+ _DEDENT"""
    t = expect(t, "perk_def")

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing perk NAME")

    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    methods: List[PerkMethodSignature] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "perk_method":
            methods.append(parse_perk_method_signature(child, ast_builder))

    if not methods:
        ice(t, "perk must have at least one method")

    attach_docs(t.children, methods, ast_builder)

    marked, public_span = read_public(t.children)

    return PerkDef(
        name=str(name_tok),
        methods=methods,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
        is_public=declared_public("perk", marked),
        public_span=public_span,
    )


def parse_perk_method_signature(t: Tree, ast_builder: 'ASTBuilder') -> PerkMethodSignature:
    """Parse perk_method: FN NAME "(" [parameters] ")" type ["|" type] _NEWLINE"""
    t = expect(t, "perk_method")

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing method NAME")

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    params_node = first_tree(t.children, "parameters")
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))

    # The return type, then the optional `| E` channel -- the same two-type read
    # `parse_funcdef` does, because the contract and the implementation now declare
    # the channel in the same shape.
    type_nodes = [child for child in t.children
                  if isinstance(child, Tree)
                  and (child.data in TYPE_NODE_NAMES or child.data == "name_t")]

    if not type_nodes:
        ice(t, "missing return type")

    return_type_node = type_nodes[0]
    err_type_node = type_nodes[1] if len(type_nodes) >= 2 else None

    return_type = ast_builder._parse_type(return_type_node)
    err_type = ast_builder._parse_type(err_type_node) if err_type_node is not None else None

    return PerkMethodSignature(
        name=str(name_tok),
        params=params,
        ret=return_type,
        self_mode=self_mode,
        self_mode_span=self_mode_span,
        err_type=err_type,
        err_span=span_of(err_type_node) if err_type_node is not None else None,
        loc=span_of(t),
        name_span=span_of(name_tok),
        ret_span=span_of(return_type_node),
    )


def parse_extendwithdef(t: Tree, ast_builder: 'ASTBuilder') -> ExtendWithDef:
    """Parse extend_with_def: EXTEND type WITH NAME ":" _NEWLINE _INDENT function_def+ _DEDENT"""
    t = expect(t, "extend_with_def")

    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    perk_name_tok = None
    found_type = False
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            found_type = True
        elif found_type and isinstance(child, Token) and child.type == "NAME":
            perk_name_tok = child
            break

    if perk_name_tok is None:
        ice(t, "missing perk NAME")

    methods = parse_impl_methods(t.children, ast_builder)

    if not methods:
        ice(t, "must have at least one method implementation")

    attach_docs(t.children, methods, ast_builder)
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None

    return ExtendWithDef(
        target_type=target_type,
        perk_name=str(perk_name_tok),
        methods=methods,
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        perk_name_span=span_of(perk_name_tok),
    )


def parse_handle_extend_stmt_with(t: Tree, ast_builder: 'ASTBuilder') -> ExtendWithDef:
    """Handle extend_stmt when it's a perk implementation."""

    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    suffix = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extend_with_def":
            suffix = child
            break

    if not suffix:
        ice(t, "missing extend_with_def suffix")

    perk_name_tok = first_name(suffix.children)
    if perk_name_tok is None:
        ice(suffix, "missing perk NAME")

    methods = parse_impl_methods(suffix.children, ast_builder)

    if not methods:
        ice(suffix, "must have at least one method implementation")

    attach_docs(suffix.children, methods, ast_builder)
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None

    return ExtendWithDef(
        target_type=target_type,
        perk_name=str(perk_name_tok),
        methods=methods,
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        perk_name_span=span_of(perk_name_tok),
    )


