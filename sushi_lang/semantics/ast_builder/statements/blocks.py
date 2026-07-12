"""Block parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Tree
from sushi_lang.semantics.ast import Block, Stmt
from sushi_lang.semantics.ast_builder.utils.tree_navigation import expect
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_block(t: Tree, ast_builder: 'ASTBuilder') -> Block:
    """Parse a block by routing every child through the statement dispatcher.

    This used to carry its own 14-entry tuple of statement kinds and SILENTLY SKIP
    any child not in it -- a second copy of parse_stmt's dispatch table, kept in
    sync by hand. They agreed, so nothing was dropped in practice, but adding a
    15th grammar alternative would have made a statement vanish here while
    parse_stmt would have reported it. parse_stmt is a total dispatcher: an
    unhandled node is CE0003, not silence.
    """
    t = expect(t, "block")
    statements: List[Stmt] = []

    for child in t.children:
        statements.append(ast_builder.stmt_parser.parse_stmt(child))

    return Block(statements=statements, loc=span_of(t))
