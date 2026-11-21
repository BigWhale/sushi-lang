"""Parsing for FFI `unsafe external` blocks and foreign function declarations."""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from lark import Tree, Token

from sushi_lang.semantics.ast import ExternalBlock, ExternalDecl, ExternalVar
from sushi_lang.semantics.typesys import Type
from sushi_lang.semantics.ast_builder.declarations.docs import attach_docs
from sushi_lang.semantics.ast_builder.declarations.functions import parse_params
from sushi_lang.semantics.ast_builder.declarations.signatures import (
    read_return_type, refuse_dont_panic)
from sushi_lang.semantics.ast_builder.utils.string_processing import strip_string_token
from sushi_lang.semantics.ast_builder.utils.tree_navigation import (
    first_tree, ice, is_type_node)
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_external_block(t: Tree, ast_builder: 'ASTBuilder') -> ExternalBlock:
    """Parse: UNSAFE EXTERNAL STRING [external_link] AS NAME [BECAUSE STRING] ":" ..."""
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
        abi = strip_string_token(string_tokens[0])
        abi_span = span_of(string_tokens[0])
    if name_tokens:
        namespace = str(name_tokens[0].value)
        namespace_span = span_of(name_tokens[0])
    if has_because and len(string_tokens) >= 2:
        reason = strip_string_token(string_tokens[1])

    decls: List[ExternalDecl] = []
    variables: List[ExternalVar] = []
    for child in t.children:
        if isinstance(child, Tree) and child.data == "extern_decl":
            decls.append(parse_extern_decl(child, ast_builder))
        elif isinstance(child, Tree) and child.data == "extern_var":
            variables.append(parse_extern_var(child, ast_builder))

    attach_docs(t.children, [*decls, *variables], ast_builder)
    libraries, library_spans = _link_clause(first_tree(t.children, "external_link"))

    return ExternalBlock(
        abi=abi if abi is not None else "",
        namespace=namespace if namespace is not None else "",
        reason=reason,
        decls=decls,
        variables=variables,
        abi_span=abi_span,
        namespace_span=namespace_span,
        libraries=libraries,
        library_spans=library_spans,
        loc=span_of(t),
    )


def _link_clause(node: Optional[Tree]) -> tuple[list, list]:
    """`link "z", "m"`: the names and their spans. The word is a NAME in the grammar, so
    that `link` stays an identifier; any other word is refused as the parser refuses a
    token (CE6001)."""
    if node is None:
        return [], []
    word = node.children[0]
    if not isinstance(word, Token) or word.type != "NAME":
        ice(node, "an external_link with no leading NAME")
    if word.value != "link":
        raise SyntaxDiagnostic("CE6001", span=span_of(word), token=str(word.value)).help(
            'write `link "<library>"` between the ABI and `as`')
    strings = [tok for tok in node.children[1:] if isinstance(tok, Token)]
    return [strip_string_token(tok) for tok in strings], [span_of(tok) for tok in strings]


def parse_extern_var(t: Tree, ast_builder: 'ASTBuilder') -> ExternalVar:
    """Parse: VAR type NAME "=" (STRING | extern_link_name)"""
    type_node = next((child for child in t.children if is_type_node(child)), None)
    name_tok = next((child for child in t.children
                     if isinstance(child, Token) and child.type == "NAME"), None)
    link_tok = next((child for child in t.children
                     if isinstance(child, Token) and child.type == "STRING"), None)
    if name_tok is None or type_node is None:
        ice(t, "missing the type or the NAME of an external variable")
    return ExternalVar(
        name=str(name_tok.value),
        ty=ast_builder._parse_type(type_node),
        link_name=strip_string_token(link_tok) if link_tok is not None else "",
        link_expr=_link_name_expr(first_tree(t.children, "extern_link_name")),
        name_span=span_of(name_tok),
        type_span=span_of(type_node),
        loc=span_of(t),
    )


def _link_name_expr(node: Optional[Tree]):
    """`= NAME` or `= alias.NAME`: the constant a link name is read from (#1089)."""
    if node is None:
        return None
    from sushi_lang.semantics.ast import MemberAccess, Name
    names = [tok for tok in node.children if isinstance(tok, Token)]
    if len(names) == 1:
        return Name(id=str(names[0].value), loc=span_of(names[0]))
    return MemberAccess(receiver=Name(id=str(names[0].value), loc=span_of(names[0])),
                        member=str(names[1].value), loc=span_of(node))


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
    link_expr = _link_name_expr(first_tree(t.children, "extern_link_name"))
    if link_tok is None and link_expr is None:
        ice(t, "missing link-name STRING")
    refuse_dont_panic(t.children, "extern", ast_builder)

    params_node = first_tree(t.children, "extern_params")
    params = parse_params(params_node, ast_builder) if params_node else []
    is_variadic = bool(
        params_node is not None
        and any(isinstance(c, Token) and c.type == "ELLIPSIS"
                for c in params_node.children)
    )

    ret_node = None
    for child in t.children:
        if is_type_node(child):
            ret_node = child
            break
    ret_ty: Optional[Type] = (read_return_type(ret_node, ast_builder, takes_never=True)
                              if ret_node is not None else None)

    return ExternalDecl(
        name=str(name_tok.value),
        params=params,
        ret=ret_ty,
        link_name=strip_string_token(link_tok) if link_tok is not None else "",
        link_expr=link_expr,
        is_variadic=is_variadic,
        name_span=span_of(name_tok),
        ret_span=span_of(ret_node),
        loc=span_of(t),
    )
