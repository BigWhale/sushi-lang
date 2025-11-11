"""Perk definition and implementation parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
from lark import Tree, Token
from semantics.ast import PerkDef, PerkMethodSignature, ExtendWithDef, FuncDef, BoundedTypeParam
from semantics.typesys import TYPE_NODE_NAMES
from semantics.ast_builder.utils.tree_navigation import first_name, first_tree
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_perkdef(t: Tree, ast_builder: 'ASTBuilder') -> PerkDef:
    """Parse perk_def: PERK NAME [type_params] ":" _NEWLINE _INDENT perk_method+ _DEDENT"""
    assert t.data == "perk_def"

    # Extract perk name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("perk_def: missing perk NAME")

    # Extract type parameters if present
    type_params_node = first_tree(t.children, "type_params")
    type_params = parse_bounded_type_params(type_params_node) if type_params_node else None

    # Extract method signatures
    methods: List[PerkMethodSignature] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "perk_method":
            methods.append(parse_perk_method_signature(child, ast_builder))

    if not methods:
        raise NotImplementedError("perk_def: perk must have at least one method")

    return PerkDef(
        name=str(name_tok),
        methods=methods,
        type_params=type_params,
        loc=span_of(t),
        name_span=span_of(name_tok),
    )


def parse_perk_method_signature(t: Tree, ast_builder: 'ASTBuilder') -> PerkMethodSignature:
    """Parse perk_method: FN NAME "(" [parameters] ")" type _NEWLINE"""
    assert t.data == "perk_method"

    # Extract method name
    name_tok = first_name(t.children)
    if name_tok is None:
        raise NotImplementedError("perk_method: missing method NAME")

    # Extract parameters
    from semantics.ast_builder.declarations.functions import parse_params
    params_node = first_tree(t.children, "parameters")
    params = parse_params(params_node, ast_builder) if params_node else []

    # Extract return type
    return_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            return_type_node = child
            break

    if return_type_node is None:
        raise NotImplementedError("perk_method: missing return type")

    return_type = ast_builder._parse_type(return_type_node)

    return PerkMethodSignature(
        name=str(name_tok),
        params=params,
        ret=return_type,
        loc=span_of(t),
        name_span=span_of(name_tok),
        ret_span=span_of(return_type_node),
    )


def parse_extendwithdef(t: Tree, ast_builder: 'ASTBuilder') -> ExtendWithDef:
    """Parse extend_with_def: EXTEND type WITH NAME ":" _NEWLINE _INDENT function_def+ _DEDENT"""
    assert t.data == "extend_with_def"

    # Extract target type (first type node)
    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    # Extract perk name (the NAME after WITH)
    # The first NAME is the type, so we need to find the token after WITH
    perk_name_tok = None
    found_type = False
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            found_type = True
        elif found_type and isinstance(child, Token) and child.type == "NAME":
            perk_name_tok = child
            break

    if perk_name_tok is None:
        raise NotImplementedError("extend_with_def: missing perk NAME")

    # Extract method implementations (function_def nodes)
    from semantics.ast_builder.declarations.functions import parse_funcdef
    methods: List[FuncDef] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "function_def":
            methods.append(parse_funcdef(child, ast_builder))

    if not methods:
        raise NotImplementedError("extend_with_def: must have at least one method implementation")

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
    # extend_stmt: EXTEND type extend_suffix
    # extend_suffix (aliased as extend_with_def): WITH NAME ":" _NEWLINE _INDENT function_def+ _DEDENT

    # Extract target type (first child after EXTEND)
    target_type_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            target_type_node = child
            break

    # Find the extend_with_def suffix
    suffix = None
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extend_with_def":
            suffix = child
            break

    if not suffix:
        raise NotImplementedError("extend_stmt: missing extend_with_def suffix")

    # Extract perk name (the NAME after WITH in suffix)
    perk_name_tok = first_name(suffix.children)
    if perk_name_tok is None:
        raise NotImplementedError("extend_stmt (with): missing perk NAME")

    # Extract method implementations (function_def nodes)
    from semantics.ast_builder.declarations.functions import parse_funcdef
    methods = []
    for child in suffix.children:
        if isinstance(child, Tree) and child.data == "function_def":
            methods.append(parse_funcdef(child, ast_builder))

    if not methods:
        raise NotImplementedError("extend_stmt (with): must have at least one method implementation")

    # Parse target type
    target_type = ast_builder._parse_type(target_type_node) if target_type_node else None

    return ExtendWithDef(
        target_type=target_type,
        perk_name=str(perk_name_tok),
        methods=methods,
        loc=span_of(t),
        target_type_span=span_of(target_type_node),
        perk_name_span=span_of(perk_name_tok),
    )


def parse_bounded_type_params(type_params_node: Optional[Tree]) -> Optional[List[BoundedTypeParam]]:
    """Parse type_params node - delegates to generics module."""
    from semantics.ast_builder.types.generics import parse_bounded_type_params as _parse_bounded
    return _parse_bounded(type_params_node)
