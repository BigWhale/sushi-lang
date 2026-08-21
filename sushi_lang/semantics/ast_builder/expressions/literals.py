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


_DECIMAL = frozenset("0123456789")
_RADIX_DIGITS = {
    "INT": _DECIMAL,
    "FLOAT": _DECIMAL,
    "HEX_INT": frozenset("0123456789abcdefABCDEF"),
    "BIN_INT": frozenset("01"),
    "OCT_INT": frozenset("01234567"),
}
_PREFIXED = frozenset({"HEX_INT", "BIN_INT", "OCT_INT"})


def _suggest(text: str, digits: frozenset, prefix: int) -> str:
    """The same literal with every badly placed underscore dropped."""
    out = list(text[:prefix])
    for i, ch in enumerate(text[prefix:], start=prefix):
        if ch != "_":
            out.append(ch)
        elif (out and out[-1] in digits
              and i + 1 < len(text) and text[i + 1] in digits):
            out.append(ch)
    return "".join(out)


def normalize_numeric(tok: Token) -> str:
    """Check underscore placement, then return the literal with underscores removed.

    The grammar's numeric terminals match a permissive superset, so this is where
    the rule lives: one underscore, between two digits of the literal's own radix.
    Every consumer of a numeric token arrives here -- an expression, an array size
    and a match arm -- so no position can be spelled more loosely than another.
    """
    text = str(tok.value)
    digits = _RADIX_DIGITS.get(tok.type, _DECIMAL)
    prefix = 2 if tok.type in _PREFIXED else 0

    for i in range(prefix, len(text)):
        if text[i] != "_":
            continue
        after = text[i + 1] if i + 1 < len(text) else ""
        before = text[i - 1] if i > prefix else ""
        if after == "_" or before == "_":
            reason = "only one underscore between digits"
        elif i == prefix:
            # A prefixed literal has somewhere for the underscore to hide; a bare
            # one cannot start with it, because the terminal begins with a digit.
            reason = ("underscore cannot follow the base prefix" if prefix
                      else "underscore must separate digits")
        elif before not in digits or after not in digits:
            reason = "underscore must separate digits"
        else:
            continue
        raise SyntaxDiagnostic("CE6006", span=span_of(tok), literal=text,
                               reason=reason).help(
            f"write '{_suggest(text, digits, prefix)}'")

    return text.replace("_", "")


def expr_from_token(tok: Token, ast_builder: 'ASTBuilder') -> Expr:
    """Map a single token to an Expr (literals and names)."""
    t = tok.type

    if t == "INT":
        # The leading-zero test reads the NORMALIZED digits: `0_77` is the same
        # C-style octal as `077`, and the underscore must not hide it.
        digits = normalize_numeric(tok)
        if len(digits) > 1 and digits[0] == '0' and digits[1].isdigit():
            raise SyntaxDiagnostic("CE2071", span=span_of(tok), literal=tok.value,
                                   octal=digits.lstrip('0') or '0')
        return IntLit(value=int(digits), radix=10, loc=span_of(tok))

    if t == "HEX_INT":
        return IntLit(value=int(normalize_numeric(tok), 16), radix=16, loc=span_of(tok))

    if t == "BIN_INT":
        return IntLit(value=int(normalize_numeric(tok), 2), radix=2, loc=span_of(tok))

    if t == "OCT_INT":
        return IntLit(value=int(normalize_numeric(tok), 8), radix=8, loc=span_of(tok))

    if t == "FLOAT":
        return FloatLit(value=float(normalize_numeric(tok)), loc=span_of(tok))

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
