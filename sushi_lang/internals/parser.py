"""Lark parser setup and AST construction."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import Any, Optional

from lark import Lark, Token, UnexpectedInput
from lark.exceptions import LarkError

from sushi_lang.internals.diagnostics import SushiError, SyntaxDiagnostic
from sushi_lang.internals.parse_errors import lark_to_diagnostic
from sushi_lang.internals.indenter import LangIndenter
from sushi_lang.internals.report import Span, span_of
from sushi_lang.semantics.ast_builder import ASTBuilder

GRAMMAR_PATH = Path(__file__).parent.parent / "grammar.lark"

# The only postlexer is the indentation handler. Generics use `@(...)`, which
# closes on a real `)`, so there is no `>>` ambiguity and no generic-type
# postlexer to chain in front of it.

DOC_OPEN = "##:"
DOC_CLOSE = ":##"


def _delimiter_span(token: Token) -> Optional[Span]:
    """The span of a bare `##:` or `:##`, which is three columns wide."""
    return span_of(token)


def _reject_unclosed_doc_block(token: Token) -> Token:
    """CE6011: a `##:` that reaches the lexer opened a block nothing closes."""
    raise SyntaxDiagnostic("CE6011", span=_delimiter_span(token))


def _reject_stray_doc_close(token: Token) -> Token:
    """CE6012: a `:##` that reaches the lexer closes a block nothing opened."""
    raise SyntaxDiagnostic("CE6012", span=_delimiter_span(token))


def _reject_nested_doc_open(token: Token) -> Token:
    """CE6013: a line-initial `##:` interior to a block, which cannot be a token.

    The block that is broken is the OUTER one -- it swallowed everything between
    the two openers -- so the caret goes on the inner opener and a note goes on
    the outer. Rendering this with a single location would be a regression.
    """
    start_line = getattr(token, "line", 1) or 1
    start_col = getattr(token, "column", 1) or 1

    for offset, line in enumerate(str(token.value).split("\n")):
        if offset == 0 or not line.lstrip().startswith(DOC_OPEN):
            continue
        col = len(line) - len(line.lstrip()) + 1
        inner = Span(start_line + offset, col, start_line + offset, col + len(DOC_OPEN))
        outer = Span(start_line, start_col, start_line, start_col + len(DOC_OPEN))
        raise SyntaxDiagnostic("CE6013", span=inner).note(
            "the enclosing documentation block opens here", outer)

    return token


@lru_cache(maxsize=1)
def build_parser() -> Lark:
    """The Lark parser for `grammar.lark`. One instance parses any number of sources.

    The three doc-block diagnostics come from `lexer_callbacks`, which fire for an
    `%ignore`d terminal exactly as they do for a kept one. That is one mechanism for
    every delimiter mistake, in every position, with the caret on the delimiter --
    see docs/design/documentation.md section 4.
    """
    kwargs: dict[str, Any] = dict(
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=LangIndenter(),
        lexer="basic",
        lexer_callbacks={
            "DOC_OPEN": _reject_unclosed_doc_block,
            "DOC_CLOSE": _reject_stray_doc_close,
            "DOC_BLOCK": _reject_nested_doc_open,
        },
    )
    # Lark.open raises GrammarError if grammar.lark itself is broken -- an ICE.
    return Lark.open(str(GRAMMAR_PATH), **kwargs)


def _string_opens_a_hole_before(line_text: str, col: int) -> bool:
    """Whether a STRING that opens an interpolation hole stands before this column.

    That is the #502 shape: a double-quoted literal inside a hole closed the
    OUTER literal, so the lexer handed the parser garbage and the error names a
    token nobody wrote. The lexer's own STRING pattern finds the tokens; a hole
    is open when the token's content holds more unescaped `{` than `}`.
    """
    import re

    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', line_text[:max(col - 1, 0)]):
        content = match.group(0)[1:-1]
        opens = closes = 0
        i = 0
        while i < len(content):
            ch = content[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "{":
                opens += 1
            elif ch == "}":
                closes += 1
            i += 1
        if opens > closes:
            return True
    return False


def parse_error_hint(e: UnexpectedInput, src: str = "") -> Optional[str]:
    """Advice for a parse failure the grammar cannot phrase itself. None if none applies."""
    # The if/elif grammar (`IF "(" expr ")" ...`) is the only place that fails
    # with LPAR as the SOLE expected token: after the keyword the parser demands
    # `(`. Gate the parentheses hint on that, so an unrelated error that merely
    # lists LPAR among several alternatives no longer gets a misleading
    # "missing parentheses around if" message.
    expected = getattr(e, "expected", None)
    if expected is not None and set(expected) == {"LPAR"}:
        return "use 'if (condition):' instead of 'if condition:'"

    line = getattr(e, "line", None)
    col = getattr(e, "column", None)
    if src and line is not None and col is not None:
        lines = src.splitlines()
        if 1 <= line <= len(lines) and _string_opens_a_hole_before(lines[line - 1], col):
            return ("a double-quoted string cannot stand inside an interpolation "
                    "hole; use single quotes inside the hole, or bind the "
                    "expression to a local first")

    return None


def parse_to_ast(src: str, dump_parse: bool = False):
    """Parse source code into an AST."""
    try:
        tree = build_parser().parse(src)
    except SushiError:
        raise
    except LarkError as e:
        hint = parse_error_hint(e, src) if isinstance(e, UnexpectedInput) else None
        raise lark_to_diagnostic(e, hint) from e

    if dump_parse:
        print(tree.pretty())

    ast_builder = ASTBuilder()
    return ast_builder.build(tree), tree
