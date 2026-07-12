"""Translate Lark's exceptions into Sushi's diagnostic channel.

Every parse failure becomes a spanned CE6xxx `SyntaxDiagnostic`, so a syntax
error renders with a code, a location and caret art exactly like a type error --
instead of Lark's raw dump on stderr, which bypassed the reporter entirely.
"""
from __future__ import annotations

from typing import Optional

from lark import UnexpectedCharacters, UnexpectedEOF, UnexpectedInput, UnexpectedToken
from lark.exceptions import GrammarError, LexError, VisitError

from sushi_lang.internals.diagnostics import InternalCompilerError, SushiError, SyntaxDiagnostic
from sushi_lang.internals.report import Span

# The parser speaks in terminal names. These are the ones a user is likely to see.
# Two phrasings: the offending token is quoted, the expected list reads as prose.
TOKEN_NAMES = {
    "_NEWLINE": "newline", "_INDENT": "indent", "_DEDENT": "dedent",
    "$END": "end of file",
}

TERMINAL_NAMES = {
    "LPAR": "'('", "RPAR": "')'", "LSQB": "'['", "RSQB": "']'",
    "COLON": "':'", "COMMA": "','", "DOT": "'.'", "EQUAL": "'='",
    "_NEWLINE": "a newline", "_INDENT": "an indented block", "_DEDENT": "a dedent",
    "NAME": "a name", "INT": "an integer", "STRING": "a string",
    "$END": "end of file",
}

# Beyond this, the expected-terminal list is wallpaper, not help.
MAX_EXPECTED = 8


def _describe_token(token) -> str:
    """A token as the user would recognise it, not as the lexer names it."""
    if token is None:
        return "input"
    text = str(token)
    if not text.strip():
        return TOKEN_NAMES.get(token.type, token.type.lower())
    return text


def _describe_terminal(name: str) -> str:
    return TERMINAL_NAMES.get(name, name)


def _span_of_exception(exc) -> Optional[Span]:
    """Map a Lark exception's position to a Span, matching report.span_of's convention."""
    line = getattr(exc, "line", None)
    col = getattr(exc, "column", None)
    if line is None or col is None:
        return None

    end_line, end_col = line, col
    token = getattr(exc, "token", None)
    if token is not None:
        token_end_line = getattr(token, "end_line", None)
        token_end_col = getattr(token, "end_column", None)
        # A token that spans lines (a newline carrying its indent) would draw a
        # caret run across the whole rest of the file. Keep it on its own line.
        if token_end_line == line and token_end_col is not None:
            end_col = max(col, token_end_col)

    return Span(line, col, end_line, end_col)


def _with_expected(diag: SushiError, exc) -> SushiError:
    expected = getattr(exc, "expected", None) or []
    if 0 < len(expected) <= MAX_EXPECTED:
        names = ", ".join(sorted(_describe_terminal(str(t)) for t in expected))
        diag.note(f"expected one of: {names}")
    return diag


def lark_to_diagnostic(exc: Exception, hint: Optional[str] = None) -> SushiError:
    """Translate any Lark exception into a diagnostic. Never returns None."""
    if isinstance(exc, VisitError):
        return lark_to_diagnostic(exc.orig_exc, hint)

    if isinstance(exc, GrammarError):
        return InternalCompilerError("CE0008", detail=str(exc))

    # A bad dedent (CE6004) is raised by LangIndenter, which is the only place
    # that knows where it happened -- DedentError itself carries no position.

    if isinstance(exc, UnexpectedEOF):
        return _with_expected(SyntaxDiagnostic("CE6003", span=_span_of_exception(exc)), exc)

    if isinstance(exc, UnexpectedToken):
        span = _span_of_exception(exc)
        token = getattr(exc, "token", None)
        # LALR reports a truncated file as an unexpected $END token, not an EOF.
        if token is not None and token.type == "$END":
            diag = SyntaxDiagnostic("CE6003", span=span)
        else:
            diag = SyntaxDiagnostic("CE6001", span=span, token=_describe_token(token))
        _with_expected(diag, exc)
        if hint:
            diag.help(hint)
        return diag

    if isinstance(exc, UnexpectedCharacters):
        return SyntaxDiagnostic("CE6002", span=_span_of_exception(exc),
                                char=getattr(exc, "char", "?"))

    if isinstance(exc, (LexError, UnexpectedInput)):
        return SyntaxDiagnostic("CE6005", span=_span_of_exception(exc))

    raise TypeError(f"not a Lark exception: {type(exc).__name__}")
