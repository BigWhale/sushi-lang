"""Lark parser setup and AST construction."""
from __future__ import annotations

from pathlib import Path

from lark import Lark, UnexpectedInput

from sushi_lang.semantics.ast_builder import ASTBuilder

GRAMMAR_PATH = Path(__file__).parent.parent / "grammar.lark"


class ChainedPostlexer:
    """Chain generic type lexer and indenter postlexers."""

    def __init__(self):
        from sushi_lang.internals.indenter import LangIndenter
        from sushi_lang.internals.generic_lexer import GenericTypeLexer

        self.generic_lexer = GenericTypeLexer()
        self.indenter = LangIndenter()
        self.always_accept = self.indenter.always_accept

    def process(self, stream):
        # First split >> into > > for nested generics
        stream = self.generic_lexer.process(stream)
        # Then handle indentation
        return self.indenter.process(stream)


def improve_parse_error(e: UnexpectedInput) -> str:
    """Improve parsing error messages for common cases."""
    error_text = str(e)

    if "Expected one of:" in error_text and "LPAR" in error_text:
        lines = error_text.split('\n')
        location_line = lines[0] if lines else ""

        import re
        line_match = re.search(r'at line (\d+)', location_line)
        if line_match:
            return f"{location_line}\nParsing error: Missing parentheses around if/elif condition.\nHint: Use 'if (condition):' instead of 'if condition:'"

    return error_text


def parse_to_ast(src: str, dump_parse: bool = False):
    """Parse source code into an AST.

    Returns:
        Tuple of (ast, parse_tree).
    """
    kwargs = dict(
        parser="lalr",
        propagate_positions=True,
        maybe_placeholders=False,
        postlex=ChainedPostlexer(),
        lexer="basic",
    )
    parser = Lark.open(str(GRAMMAR_PATH), **kwargs)
    tree = parser.parse(src)
    if dump_parse:
        print(tree.pretty())

    ast_builder = ASTBuilder()
    return ast_builder.build(tree), tree
