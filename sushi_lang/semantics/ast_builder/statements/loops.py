"""Loop statement parsing (foreach)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import Foreach
from sushi_lang.semantics.typesys import Type, TYPE_NODE_NAMES
from sushi_lang.internals.report import span_of, Span

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_foreach_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Foreach:
    """Parse foreach_stmt: FOREACH "(" [type] NAME "in" expr ")" ":" block"""
    children = node.children
    idx = 0

    # Skip the FOREACH token (first child)
    if idx < len(children) and isinstance(children[idx], Token) and children[idx].type == "FOREACH":
        idx += 1

    # Check if next child is a type
    item_type: Optional[Type] = None
    item_type_span: Optional[Span] = None
    if idx < len(children) and isinstance(children[idx], Tree) and children[idx].data in TYPE_NODE_NAMES:
        type_tree = children[idx]
        item_type = ast_builder._parse_type(type_tree)
        item_type_span = span_of(type_tree)
        idx += 1

    # Next is NAME
    if idx >= len(children) or not isinstance(children[idx], Token) or children[idx].type != "NAME":
        raise ValueError(f"foreach_stmt expects NAME at index {idx}, got {children[idx] if idx < len(children) else 'nothing'}")
    name_tok = children[idx]
    item_name = name_tok.value
    item_name_span = span_of(name_tok)
    idx += 1

    # Next is the iterable expression (skip any "in" tokens if present)
    while idx < len(children) and isinstance(children[idx], Token) and children[idx].value == "in":
        idx += 1

    iterable_tree = children[idx]
    iterable = ast_builder._expr(iterable_tree)
    idx += 1

    # Last is the block
    block_tree = children[idx]
    body = ast_builder._block(block_tree)

    return Foreach(
        item_name=item_name,
        item_type=item_type,
        iterable=iterable,
        body=body,
        item_name_span=item_name_span,
        item_type_span=item_type_span,
        loc=span_of(node)
    )
