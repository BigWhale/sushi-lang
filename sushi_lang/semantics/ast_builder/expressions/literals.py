"""Literal expression parsing (integers, floats, booleans, strings, names)."""
from __future__ import annotations
from typing import TYPE_CHECKING
from lark import Token
from sushi_lang.semantics.ast import Expr, IntLit, FloatLit, BoolLit, Name
from sushi_lang.semantics.ast_builder.utils.string_processing import parse_string_token
from sushi_lang.semantics.ast_builder.utils.tree_navigation import unhandled
from sushi_lang.internals.diagnostics import SyntaxDiagnostic
from sushi_lang.internals.report import span_of

if TYPE_CHECKING:
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def expr_from_token(tok: Token, ast_builder: 'ASTBuilder') -> Expr:
    """Map a single token to an Expr (literals and names).

    Handles: INT, HEX_INT, BIN_INT, OCT_INT, FLOAT, TRUE, FALSE, STRING, NAME
    """
    t = tok.type

    if t == "INT":
        # Check for C-style octal literals (leading zero) and reject them
        if len(tok.value) > 1 and tok.value[0] == '0' and tok.value[1].isdigit():
            raise SyntaxDiagnostic("CE2071", span=span_of(tok), literal=tok.value,
                                   octal=tok.value.lstrip('0') or '0')
        return IntLit(value=int(tok.value), radix=10, loc=span_of(tok))

    if t == "HEX_INT":
        hex_str = tok.value.replace('_', '')
        return IntLit(value=int(hex_str, 16), radix=16, loc=span_of(tok))

    if t == "BIN_INT":
        bin_str = tok.value.replace('_', '')
        return IntLit(value=int(bin_str, 2), radix=2, loc=span_of(tok))

    if t == "OCT_INT":
        oct_str = tok.value.replace('_', '')
        return IntLit(value=int(oct_str, 8), radix=8, loc=span_of(tok))

    if t == "FLOAT":
        return FloatLit(value=float(tok.value), loc=span_of(tok))

    if t == "TRUE":
        return BoolLit(value=True, loc=span_of(tok))

    if t == "FALSE":
        return BoolLit(value=False, loc=span_of(tok))

    if t == "STRING":
        return parse_string_token(tok, ast_builder)

    if t == "CHAR_STRING":
        return parse_string_token(tok, ast_builder)

    if t == "NAME":
        return Name(id=str(tok.value), loc=span_of(tok))

    unhandled(tok)
