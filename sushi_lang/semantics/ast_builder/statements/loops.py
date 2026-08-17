"""Loop statement parsing (foreach)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from lark import Tree, Token
from sushi_lang.semantics.ast import Foreach, Expand
from sushi_lang.semantics.typesys import ReferenceType, Type, TYPE_NODE_NAMES
from sushi_lang.semantics.ast_builder.utils.tree_navigation import ice
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
        ice(node, f"foreach_stmt expects NAME at index {idx}, got {children[idx] if idx < len(children) else 'nothing'}")
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

    # A reference-typed item (`foreach(poke i32 r in ...)`) is the long spelling of
    # the marker form (`foreach(poke r in ...)`) -- normalize it, so every downstream
    # pass sees ONE spelling: `item_borrow` set, `item_type` the referent (#300).
    item_borrow: Optional[str] = None
    item_borrow_span: Optional[Span] = None
    if isinstance(item_type, ReferenceType):
        item_borrow = item_type.mutability.value
        item_borrow_span = item_type_span
        item_type = item_type.referenced_type

    return Foreach(
        item_name=item_name,
        item_type=item_type,
        iterable=iterable,
        body=body,
        item_name_span=item_name_span,
        item_type_span=item_type_span,
        item_borrow=item_borrow,
        item_borrow_span=item_borrow_span,
        loc=span_of(node)
    )


def parse_foreach_ref(node: Tree, ast_builder: 'ASTBuilder') -> Foreach:
    """Parse foreach_ref: FOREACH "(" BORROW_MODE NAME "in" expr ")" ":" block

    The reference-binding marker form (#300 phase 1): `foreach(poke r in rows.iter())`
    binds `r` as a pointer into the container's element storage. The element type is
    inferred from the iterable, exactly like the untyped plain form.
    """
    children = node.children
    idx = 0

    if idx < len(children) and isinstance(children[idx], Token) and children[idx].type == "FOREACH":
        idx += 1

    if idx >= len(children) or not isinstance(children[idx], Token) or children[idx].type != "BORROW_MODE":
        ice(node, f"foreach_ref expects BORROW_MODE at index {idx}")
    item_borrow = children[idx].value
    item_borrow_span = span_of(children[idx])
    idx += 1

    if idx >= len(children) or not isinstance(children[idx], Token) or children[idx].type != "NAME":
        ice(node, f"foreach_ref expects NAME at index {idx}")
    name_tok = children[idx]
    item_name = name_tok.value
    item_name_span = span_of(name_tok)
    idx += 1

    while idx < len(children) and isinstance(children[idx], Token) and children[idx].value == "in":
        idx += 1

    iterable_tree = children[idx]
    iterable = ast_builder._expr(iterable_tree)
    idx += 1

    block_tree = children[idx]
    body = ast_builder._block(block_tree)

    return Foreach(
        item_name=item_name,
        item_type=None,
        iterable=iterable,
        body=body,
        item_name_span=item_name_span,
        item_type_span=None,
        item_borrow=item_borrow,
        item_borrow_span=item_borrow_span,
        loc=span_of(node)
    )


def parse_expand_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Expand:
    """Parse expand_stmt: EXPAND "(" NAME "in" expr ")" ":" block

    Compile-time analog of `foreach`: the body is unrolled once per element of a
    value pack. Mirrors `parse_foreach_stmt`'s extraction of loop var, iterable,
    and block.
    """
    children = node.children
    idx = 0

    # Skip the EXPAND token (first child)
    if idx < len(children) and isinstance(children[idx], Token) and children[idx].type == "EXPAND":
        idx += 1

    # Next is the binding NAME
    if idx >= len(children) or not isinstance(children[idx], Token) or children[idx].type != "NAME":
        ice(node, f"expand_stmt expects NAME at index {idx}, got {children[idx] if idx < len(children) else 'nothing'}")
    name_tok = children[idx]
    var = name_tok.value
    var_span = span_of(name_tok)
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

    return Expand(
        var=var,
        iterable=iterable,
        body=body,
        var_span=var_span,
        loc=span_of(node),
    )
