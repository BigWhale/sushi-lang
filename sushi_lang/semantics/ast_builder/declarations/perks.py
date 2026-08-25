"""Perk definition and implementation parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Tree, Token
from sushi_lang.semantics.ast import PerkDef, PerkMethodSignature, ExtendWithDef, FuncDef
from sushi_lang.semantics.typesys import TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_name, first_tree, ice, expect
from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
from sushi_lang.semantics.ast_builder.types.generics import parse_bounded_type_params
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


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

    return PerkDef(
        name=str(name_tok),
        methods=methods,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
    )


def parse_perk_method_signature(t: Tree, ast_builder: 'ASTBuilder') -> PerkMethodSignature:
    """Parse perk_method: FN NAME "(" [parameters] ")" type _NEWLINE"""
    t = expect(t, "perk_method")

    name_tok = first_name(t.children)
    if name_tok is None:
        ice(t, "missing method NAME")

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_params, strip_self_param
    params_node = first_tree(t.children, "parameters")
    params = parse_params(params_node, ast_builder) if params_node else []
    self_mode, self_mode_span, params = strip_self_param(params, span_of(t))

    return_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            return_type_node = child
            break

    if return_type_node is None:
        ice(t, "missing return type")

    return_type = ast_builder._parse_type(return_type_node)

    return PerkMethodSignature(
        name=str(name_tok),
        params=params,
        ret=return_type,
        self_mode=self_mode,
        self_mode_span=self_mode_span,
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

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_funcdef
    methods: List[FuncDef] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "function_def":
            methods.append(parse_funcdef(child, ast_builder))

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

    from sushi_lang.semantics.ast_builder.declarations.functions import parse_funcdef
    methods = []
    for child in suffix.children:
        if isinstance(child, Tree) and child.data == "function_def":
            methods.append(parse_funcdef(child, ast_builder))

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


