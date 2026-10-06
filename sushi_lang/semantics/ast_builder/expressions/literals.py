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


def normalize_numeric(tok: Token, ast_builder: 'ASTBuilder') -> str:
    """Check underscore placement, then return the literal with underscores removed.

    The grammar's numeric terminals match a permissive superset, so this is where
    the rule lives: one underscore, between two digits of the literal's own radix.
    Every consumer of a numeric token arrives here -- an expression, an array size
    and a match arm -- so no position can be spelled more loosely than another.

    A badly placed underscore recovers to the digits alone, which is the literal the
    help asks for, so the next one is found in the same run.
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
        return ast_builder.recover(
            SyntaxDiagnostic("CE6006", span=span_of(tok), literal=text,
                             reason=reason).help(
                f"write '{_suggest(text, digits, prefix)}'"),
            text.replace("_", ""))

    return text.replace("_", "")


_BYTE_ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34}
_HEX = frozenset("0123456789abcdefABCDEF")
_ONE_BYTE = "a byte literal holds one character or one escape"


def _read_byte(body: str) -> tuple[int | None, int, str | None]:
    """The first character or escape of a byte literal's body.

    Answers its value (None when it has none), the count of characters it takes, and
    the reason it is malformed (None when it is not).
    """
    if body[0] != "\\":
        return ord(body[0]), 1, None
    code = body[1]
    if code == "x":
        digits = body[2:4]
        if len(digits) != 2 or not set(digits) <= _HEX:
            return None, len(body), "'\\x' takes exactly two hex digits"
        return int(digits, 16), 4, None
    if code in _BYTE_ESCAPES:
        return _BYTE_ESCAPES[code], 2, None
    return None, 2, f"unknown escape '\\{code}'"


def parse_byte_literal(tok: Token, ast_builder: 'ASTBuilder') -> IntLit:
    """The byte literal `a'x'`: one character from 0 to 127, or one escape (0 to 255).

    The terminal matches a permissive superset, so the rule lives here: an empty body,
    more than one character or escape, an unknown escape and a short `\\x` are CE6014,
    and one character above 127 is CE6015. Each recovers to the value of the first
    character or escape (else 0), with the same spelling, so the walk goes on.
    """
    spelling = str(tok.value)
    body = spelling[2:-1]

    def literal(value: int) -> IntLit:
        return IntLit(value=value, radix=10, loc=span_of(tok), byte_spelling=spelling)

    def malformed(reason: str, value: int) -> IntLit:
        return ast_builder.recover(
            SyntaxDiagnostic("CE6014", span=span_of(tok), literal=spelling, reason=reason),
            literal(value))

    if not body:
        return malformed(f"the literal is empty; {_ONE_BYTE}", 0)
    value, used, reason = _read_byte(body)
    if reason is not None:
        return malformed(reason, 0)
    assert value is not None
    if used < len(body):
        return malformed(_ONE_BYTE, value)
    if value > 127 and body[0] != "\\":
        char = body[0]
        encoded = char.encode("utf-8")
        listed = " ".join(f"0x{b:02x}" for b in encoded)
        help_text = f"'{char}' is {len(encoded)} bytes in UTF-8 ({listed}); "
        if value <= 255:
            help_text += f"write a'\\x{value:02x}' for the byte {value}, or "
        help_text += f'compare with the string "{char}"'
        return ast_builder.recover(
            SyntaxDiagnostic("CE6015", span=span_of(tok), char=char).help(help_text),
            literal(value if value <= 255 else 0))
    return literal(value)


def expr_from_token(tok: Token, ast_builder: 'ASTBuilder') -> Expr:
    """Map a single token to an Expr (literals and names)."""
    t = tok.type

    if t == "INT":
        # The leading-zero test reads the NORMALIZED digits: `0_77` is the same
        # C-style octal as `077`, and the underscore must not hide it.
        digits = normalize_numeric(tok, ast_builder)
        if len(digits) > 1 and digits[0] == '0' and digits[1].isdigit():
            # The leading zero is dropped and the rest reads as decimal, which is
            # what the digits already say: nothing here invents a value.
            return ast_builder.recover(
                SyntaxDiagnostic("CE2071", span=span_of(tok), literal=tok.value,
                                 octal=digits.lstrip('0') or '0'),
                IntLit(value=int(digits), radix=10, loc=span_of(tok)))
        return IntLit(value=int(digits), radix=10, loc=span_of(tok))

    if t == "HEX_INT":
        return IntLit(value=int(normalize_numeric(tok, ast_builder), 16), radix=16, loc=span_of(tok))

    if t == "BIN_INT":
        return IntLit(value=int(normalize_numeric(tok, ast_builder), 2), radix=2, loc=span_of(tok))

    if t == "OCT_INT":
        return IntLit(value=int(normalize_numeric(tok, ast_builder), 8), radix=8, loc=span_of(tok))

    if t == "BYTE_CHAR":
        return parse_byte_literal(tok, ast_builder)

    if t == "FLOAT":
        return FloatLit(value=float(normalize_numeric(tok, ast_builder)), loc=span_of(tok))

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
