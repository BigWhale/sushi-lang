"""Parsing for FFI `unsafe external` blocks and foreign function declarations."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from lark import Tree, Token

from sushi_lang.semantics.ast import ExternalBlock, ExternalDecl
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.declarations.functions import parse_params
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, ice
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def _strip_string_token(tok: Token) -> str:
    """Strip surrounding quotes from a STRING token (no interpolation)."""
    raw = str(tok.value)
    if len(raw) >= 2 and raw[0] in ("\"", "'") and raw[-1] == raw[0]:
        raw = raw[1:-1]
    return raw


def parse_external_block(t: Tree, ast_builder: 'ASTBuilder') -> ExternalBlock:
    """Parse: UNSAFE EXTERNAL STRING AS NAME [BECAUSE STRING] ":" ... extern_decl+"""
    abi: Optional[str] = None
    abi_span = None
    namespace: Optional[str] = None
    namespace_span = None
    reason: Optional[str] = None

    # The first STRING is the ABI; if a BECAUSE token is present, the STRING
    # following it is the reason. NAME after AS is the namespace.
    string_tokens: List[Token] = []
    name_tokens: List[Token] = []
    has_because = False
    for child in t.children:
        if isinstance(child, Token):
            if child.type == "STRING":
                string_tokens.append(child)
            elif child.type == "NAME":
                name_tokens.append(child)
            elif child.type == "BECAUSE":
                has_because = True

    if string_tokens:
        abi = _strip_string_token(string_tokens[0])
        abi_span = span_of(string_tokens[0])
    if name_tokens:
        namespace = str(name_tokens[0].value)
        namespace_span = span_of(name_tokens[0])
    if has_because and len(string_tokens) >= 2:
        reason = _strip_string_token(string_tokens[1])

    decls: List[ExternalDecl] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extern_decl":
            decls.append(parse_extern_decl(child, ast_builder))

    return ExternalBlock(
        abi=abi if abi is not None else "",
        namespace=namespace if namespace is not None else "",
        reason=reason,
        decls=decls,
        abi_span=abi_span,
        namespace_span=namespace_span,
        loc=span_of(t),
    )


def parse_extern_decl(t: Tree, ast_builder: 'ASTBuilder') -> ExternalDecl:
    """Parse: FN NAME "(" [parameters] ")" type "=" STRING"""
    name_tok: Optional[Token] = None
    link_tok: Optional[Token] = None
    for child in t.children:
        if isinstance(child, Token):
            if child.type == "NAME" and name_tok is None:
                name_tok = child
            elif child.type == "STRING":
                link_tok = child

    if name_tok is None:
        ice(t, "missing NAME")
    if link_tok is None:
        ice(t, "missing link-name STRING")

    # The trailing `...` (untyped C varargs) lives inside the extern_params node.
    params_node = first_tree(t.children, "extern_params")
    params = parse_params(params_node, ast_builder) if params_node else []
    is_variadic = bool(
        params_node is not None
        and any(isinstance(c, Token) and c.type == "ELLIPSIS"
                for c in params_node.children)
    )

    # The return type is the single type node child.
    ret_node = None
    for child in t.children:
        if isinstance(child, Tree) and (child.data in TYPE_NODE_NAMES or child.data == "name_t"):
            ret_node = child
            break
    ret_ty: Optional[Type] = ast_builder._parse_type(ret_node) if ret_node is not None else None

    return ExternalDecl(
        name=str(name_tok.value),
        params=params,
        ret=ret_ty,
        link_name=_strip_string_token(link_tok),
        is_variadic=is_variadic,
        name_span=span_of(name_tok),
        ret_span=span_of(ret_node),
        loc=span_of(t),
    )
