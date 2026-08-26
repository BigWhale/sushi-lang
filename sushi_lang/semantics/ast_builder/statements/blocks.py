"""Block parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Token, Tree
from sushi_lang.semantics.ast import Block, Stmt
from sushi_lang.semantics.ast_builder.declarations.docs import peel_body_docs
from sushi_lang.semantics.ast_builder.utils.tree_navigation import expect
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_block(t: Tree, ast_builder: 'ASTBuilder') -> Block:
    """Parse a block by routing every child through the statement dispatcher.

    Doc blocks are peeled first: one reaching `parse_stmt` as a statement class would
    need an arm in every exhaustive statement dispatcher in the compiler.
    """
    t = expect(t, "block")
    body_doc = peel_body_docs(t.children, ast_builder)
    statements: List[Stmt] = []

    for child in t.children:
        if isinstance(child, Token) and child.type == "DOC_BLOCK":
            continue
        statements.append(ast_builder.stmt_parser.parse_stmt(child))

    block = Block(statements=statements, loc=span_of(t), doc=body_doc)
    if body_doc is not None:
        ast_builder.pending_body_docs[id(block)] = (block, body_doc)
    return block
