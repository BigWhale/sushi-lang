"""Control flow statement parsing (if, while)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List
from lark import Tree
from sushi_lang.semantics.ast import If, While, Block, Expr
from sushi_lang.semantics.ast_builder.utils.tree_navigation import first_tree, find_tree_recursive
from sushi_lang.semantics.ast_builder.utils.expression_discovery import expr_and_block
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def parse_if_stmt(node: Tree, ast_builder: 'ASTBuilder') -> If:
    """Parse if_stmt: IF expr ":" block [elif_part]* [else_part]"""
    cond_t, blk_t = expr_and_block(node)
    arms: List[tuple[Expr, Block]] = [(ast_builder._expr(cond_t), ast_builder._block(blk_t))]
    else_block: Optional[Block] = None
    for part in node.children:
        if isinstance(part, Tree) and part.data == "elif_part":
            c, b = expr_and_block(part)
            arms.append((ast_builder._expr(c), ast_builder._block(b)))
        elif isinstance(part, Tree) and part.data == "else_part":
            eb = first_tree(part.children, "block") or find_tree_recursive(part, "block")
            else_block = ast_builder._block(eb) if eb else None
    return If(arms=arms, else_block=else_block, loc=span_of(node))


def parse_while_stmt(node: Tree, ast_builder: 'ASTBuilder') -> While:
    """Parse while_stmt: WHILE expr ":" block"""
    cond_t, blk_t = expr_and_block(node)
    return While(cond=ast_builder._expr(cond_t), body=ast_builder._block(blk_t), loc=span_of(node))
