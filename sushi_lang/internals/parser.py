"""Lark parser setup and AST construction."""
from __future__ import annotations

from pathlib import Path

from typing import Any, Optional

from lark import Lark, UnexpectedInput
from lark.exceptions import LarkError

from sushi_lang.internals.diagnostics import SushiError
from sushi_lang.internals.parse_errors import lark_to_diagnostic
from sushi_lang.internals.indenter import LangIndenter
from sushi_lang.semantics.ast_builder import ASTBuilder

GRAMMAR_PATH = Path(__file__).parent.parent / "grammar.lark"

# The only postlexer is the indentation handler. Generics use `@(...)`, which
# closes on a real `)`, so there is no `>>` ambiguity and no generic-type
# postlexer to chain in front of it.


def parse_error_hint(e: UnexpectedInput) -> Optional[str]:
    """Advice for a parse failure the grammar cannot phrase itself. None if none applies."""
    # The if/elif grammar (`IF "(" expr ")" ...`) is the only place that fails
    # with LPAR as the SOLE expected token: after the keyword the parser demands
    # `(`. Gate the parentheses hint on that, so an unrelated error that merely
    # lists LPAR among several alternatives no longer gets a misleading
    # "missing parentheses around if" message.
    expected = getattr(e, "expected", None)
    if expected is not None and set(expected) == {"LPAR"}:
        return "use 'if (condition):' instead of 'if condition:'"

    return None


def parse_to_ast(src: str, dump_parse: bool = False):
    """Parse source code into an AST.

    Returns:
        Tuple of (ast, parse_tree).

    Raises:
        SushiError: any parse failure, as a spanned diagnostic.
    """
    kwargs: dict[str, Any] = dict(
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=LangIndenter(),
        lexer="basic",
    )
    try:
        # Lark.open raises GrammarError if grammar.lark itself is broken -- an ICE.
        parser = Lark.open(str(GRAMMAR_PATH), **kwargs)
        tree = parser.parse(src)
    except SushiError:
        raise
    except LarkError as e:
        hint = parse_error_hint(e) if isinstance(e, UnexpectedInput) else None
        raise lark_to_diagnostic(e, hint) from e

    if dump_parse:
        print(tree.pretty())

    ast_builder = ASTBuilder()
    return ast_builder.build(tree), tree
