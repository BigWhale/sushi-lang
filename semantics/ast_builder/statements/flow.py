"""Flow control statement parsing (break, continue)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from semantics.ast import Break, Continue
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


def parse_break_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Break:
    """Parse break_stmt: BREAK"""
    return Break(loc=span_of(node))


def parse_continue_stmt(node: Tree, ast_builder: 'ASTBuilder') -> Continue:
    """Parse continue_stmt: CONTINUE"""
    return Continue(loc=span_of(node))
