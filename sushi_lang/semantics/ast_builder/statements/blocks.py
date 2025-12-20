"""Block parsing."""
from __future__ import annotations
from typing import TYPE_CHECKING, List
from lark import Tree
from sushi_lang.semantics.ast import Block, Stmt
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_block(t: Tree, ast_builder: 'ASTBuilder') -> Block:
    """Parse block with dispatch to statement handlers."""
    assert t.data == "block"
    statements: List[Stmt] = []

    for ch in t.children:
        if not isinstance(ch, Tree):
            continue
        node = ch
        if node.data == "statement" and node.children:
            node = node.children[0]

        # Parse statement using statement parser
        if node.data in ("return_stmt", "print_stmt", "println_stmt", "let_stmt", "rebind_stmt",
                         "call_stmt", "if_stmt", "while_stmt", "foreach_stmt", "match_stmt",
                         "break_stmt", "continue_stmt", "function_def"):
            statements.append(ast_builder.stmt_parser.parse_stmt(node))

    return Block(statements=statements, loc=span_of(t))
