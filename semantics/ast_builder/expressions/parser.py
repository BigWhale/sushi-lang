"""Main expression parser coordinating specialized expression parsers."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Tree, Token
from semantics.ast import Expr, Name
from semantics.ast_builder.expressions import literals, operators, chains, calls
from internals.report import span_of

if TYPE_CHECKING:
    from semantics.ast_builder.builder import ASTBuilder


class ExpressionParser:
    """Coordinates expression parsing across specialized parsers."""

    def __init__(self, ast_builder: 'ASTBuilder'):
        """Initialize ExpressionParser with reference to ASTBuilder for recursive parsing."""
        self.ast_builder = ast_builder

    def parse_expr(self, t: Tree | Token) -> Expr:
        """Parse an expression node into an Expr object.

        Main dispatcher for all expression types.
        """
        # Tokens: delegate to literals parser
        if isinstance(t, Token):
            return literals.expr_from_token(t, self.ast_builder)

        # Trees
        tag = t.data

        # Top-level expr wrapper: descend
        if tag == "expr":
            return self.parse_expr(t.children[0])

        # Call chains: atom followed by zero or more calls/accesses
        if tag == "maybe_call":
            return chains.expr_call_chain(t, self.ast_builder)

        # Unary operators
        if tag in {"neg", "not", "bitnot", "unary"}:
            return operators.expr_unary(t, self.ast_builder)

        # Borrow expression
        if tag == "borrow":
            return operators.expr_borrow(t, self.ast_builder)

        # Binary operator chains
        if tag in {"add", "mul", "equality", "comparison", "shift",
                   "bitwise_and", "bitwise_xor", "bitwise_or",
                   "and_expr", "xor_expr", "or_expr"}:
            return operators.bin_chain(t, self.ast_builder)

        # Range expressions
        if tag == "range":
            return operators.parse_range_expr(t, self.ast_builder)

        # Cast expressions
        if tag == "cast":
            return operators.handle_cast(t, self.ast_builder)

        # Atoms
        if tag == "atom":
            return chains.expr_atom(t, self.ast_builder)

        # Legacy postfix (compatibility)
        if tag == "postfix":
            atom_expr = chains.expr_atom(t.children[0], self.ast_builder)
            if len(t.children) == 2:
                if not isinstance(atom_expr, Name):
                    raise NotImplementedError("only NAME(...) calls supported for now")
                return calls.call_from_parts(atom_expr.id, t.children[1], self.ast_builder)
            return atom_expr

        raise NotImplementedError(f"unhandled expr node: {tag}")
