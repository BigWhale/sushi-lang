"""Main expression parser coordinating specialized expression parsers."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from sushi_lang.semantics.ast import Expr
from sushi_lang.semantics.ast_builder.expressions import literals, operators, chains
from sushi_lang.semantics.ast_builder.utils.tree_navigation import unhandled

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


class ExpressionParser:
    """Coordinates expression parsing across specialized parsers."""

    def __init__(self, ast_builder: 'ASTBuilder'):
        """Initialize ExpressionParser with reference to ASTBuilder for recursive parsing."""
        self.ast_builder = ast_builder

    def parse_expr(self, t: Tree | Token) -> Expr:
        """Parse an expression node into an Expr object."""
        if isinstance(t, Token):
            return literals.expr_from_token(t, self.ast_builder)

        tag = t.data

        if tag == "expr":
            return self.parse_expr(t.children[0])

        if tag == "maybe_call":
            return chains.expr_call_chain(t, self.ast_builder)

        if tag in {"neg", "not", "bitnot", "unary"}:
            return operators.expr_unary(t, self.ast_builder)

        if tag == "borrow":
            return operators.expr_borrow(t, self.ast_builder)

        if tag in {"add", "mul", "equality", "comparison", "shift",
                   "bitwise_and", "bitwise_xor", "bitwise_or",
                   "and_expr", "xor_expr", "or_expr"}:
            return operators.bin_chain(t, self.ast_builder)

        if tag == "range":
            return operators.parse_range_expr(t, self.ast_builder)

        if tag == "cast":
            return operators.handle_cast(t, self.ast_builder)

        if tag == "atom":
            return chains.expr_atom(t, self.ast_builder)

        # `?postfix` is aliased to `maybe_call` in the grammar, so a `postfix` Tree
        # never survives into the builder. The branch that handled it was dead.

        unhandled(t)
