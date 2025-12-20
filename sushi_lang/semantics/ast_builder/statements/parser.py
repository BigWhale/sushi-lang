"""Main statement parser coordinating specialized statement parsers."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree
from sushi_lang.semantics.ast import Stmt
from sushi_lang.semantics.ast_builder.statements import io, returns, variables, control_flow, loops, flow, calls, matching

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
            "match_stmt": matching.parse_match_stmt,
            "break_stmt": flow.parse_break_stmt,
            "continue_stmt": flow.parse_continue_stmt,
        }

        # Handle nested function definitions (error case)
        if node.data == "function_def":
            raise NotImplementedError("Nested function definitions not supported yet")

        # Dispatch to appropriate handler
        handler = stmt_handlers.get(node.data)
        if handler:
            return handler(node, self.ast_builder)

        raise NotImplementedError(f"unhandled statement node: {node.data}")
