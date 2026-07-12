"""Main statement parser coordinating specialized statement parsers."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of
from sushi_lang.semantics.ast import Stmt
from sushi_lang.semantics.ast_builder.statements import io, returns, variables, control_flow, loops, flow, calls, matching
from sushi_lang.semantics.ast_builder.utils.tree_navigation import unhandled

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


class StatementParser:
    """Coordinates statement parsing across specialized parsers."""

    def __init__(self, ast_builder: 'ASTBuilder'):
        """Initialize StatementParser with reference to ASTBuilder for recursive parsing."""
        self.ast_builder = ast_builder

    def parse_stmt(self, node: Tree) -> Stmt:
        """Parse a statement node into a Stmt object.

        Main dispatcher for all statement types.
        """
        # Dispatch table for statement types
        stmt_handlers = {
            "return_stmt": returns.parse_return_stmt,
            "print_stmt": io.parse_print_stmt,
            "println_stmt": io.parse_println_stmt,
            "let_stmt": variables.parse_let_stmt,
            "rebind_stmt": variables.parse_rebind_stmt,
            "call_stmt": calls.parse_call_stmt,
            "if_stmt": control_flow.parse_if_stmt,
            "while_stmt": control_flow.parse_while_stmt,
            "foreach_stmt": loops.parse_foreach_stmt,
            "expand_stmt": loops.parse_expand_stmt,
            "match_stmt": matching.parse_match_stmt,
            "break_stmt": flow.parse_break_stmt,
            "continue_stmt": flow.parse_continue_stmt,
        }

        # A nested `fn` PARSES -- the grammar allows it inside a body -- so this is a
        # real user error, not a builder bug.
        if node.data == "function_def":
            raise SyntaxDiagnostic("CE6101", span=span_of(node)) \
                .help("move the function to the top level, or use a lambda")

        # Dispatch to appropriate handler
        handler = stmt_handlers.get(node.data)
        if handler:
            return handler(node, self.ast_builder)

        unhandled(node)
