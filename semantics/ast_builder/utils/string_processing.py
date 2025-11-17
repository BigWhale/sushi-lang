"""String processing utilities for handling escape sequences and interpolation."""
from __future__ import annotations
from typing import List, Optional, Tuple, Union, TYPE_CHECKING
from pathlib import Path
from lark import Lark, Token

if TYPE_CHECKING:
    from internals.report import Span
    from semantics.ast import Expr


def process_string_escapes(raw_string: str) -> str:
    r"""Process escape sequences in a string literal.

    Handles standard C-style escape sequences:
    - \n (newline), \t (tab), \r (carriage return)
    - \\ (backslash), \" (double quote), \' (single quote)
    - \0 (null character)
    - \xNN (hexadecimal escape, e.g., \x41 = 'A')
    - \uNNNN (Unicode escape, e.g., \u0041 = 'A')

    Args:
        raw_string: The raw string with potential escape sequences

    Returns:
        The processed string with escape sequences converted to actual characters
    """
    simple_escapes = {
        'n': '\n',
        't': '\t',
        'r': '\r',
        '\\': '\\',
        '"': '"',
        "'": "'",
        '0': '\0',
    }

    result = []
    i = 0
    while i < len(raw_string):
        if raw_string[i] == '\\' and i + 1 < len(raw_string):
            next_char = raw_string[i + 1]

            if next_char in simple_escapes:
                result.append(simple_escapes[next_char])
                i += 2
            elif next_char == 'x' and i + 3 < len(raw_string):
                try:
                    hex_value = int(raw_string[i + 2:i + 4], 16)
                    result.append(chr(hex_value))
                    i += 4
                except (ValueError, OverflowError):
                    result.append(raw_string[i])
                    i += 1
            elif next_char == 'u' and i + 5 < len(raw_string):
                try:
                    unicode_value = int(raw_string[i + 2:i + 6], 16)
                    result.append(chr(unicode_value))
                    i += 6
                except (ValueError, OverflowError):
                    result.append(raw_string[i])
                    i += 1
            else:
                result.append(raw_string[i])
                i += 1
        else:
            result.append(raw_string[i])
            i += 1

    return ''.join(result)


def parse_interpolated_string(raw_string: str, span: 'Span') -> Tuple[List[Union[str, str]], List['Span']]:
    """Parse a string with {expression} interpolations.

    Args:
        raw_string: The raw string content (without quotes)
        span: Source span for error reporting

    Returns:
        Tuple of (parts_list, expression_spans) where:
        - parts_list alternates between string literals and expression strings
        - expression_spans contains spans for each expression for parsing

    Raises:
        Exception with CE2026 error code for unterminated braces
    """
    from semantics.ast_builder.exceptions import UnterminatedInterpolationError, EmptyInterpolationError
    from internals.report import Span

    parts = []
    expr_spans = []
    current_part = []
    i = 0

    while i < len(raw_string):
        char = raw_string[i]

        if char == '{':
            parts.append(''.join(current_part))
            current_part = []

            brace_start = i
            i += 1
            brace_count = 1
            expr_start = i

            while i < len(raw_string) and brace_count > 0:
                if raw_string[i] == '{':
                    brace_count += 1
                elif raw_string[i] == '}':
                    brace_count -= 1
                i += 1

            if brace_count > 0:
                raise UnterminatedInterpolationError(
                    "unterminated interpolation in string literal",
                    span
                )

            expr_content = raw_string[expr_start:i-1]
            if not expr_content.strip():
                raise EmptyInterpolationError(
                    "empty interpolation in string literal",
                    span
                )

            parts.append(expr_content)
            expr_col_offset = span.col + 1 + brace_start + 1
            expr_span = Span(
                line=span.line,
                col=expr_col_offset,
                end_line=span.line,
                end_col=expr_col_offset + len(expr_content)
            )
            expr_spans.append(expr_span)

        else:
            current_part.append(char)
            i += 1

    parts.append(''.join(current_part))

    return parts, expr_spans


# Cached parser for interpolation expressions (lazy initialized)
_interpolation_parser: Optional[Lark] = None


def get_interpolation_parser() -> Lark:
    """Get or create a Lark parser for parsing interpolation expressions.

    This parser is specifically for parsing expression strings inside {braces}
    in interpolated strings. It uses the same grammar as the main parser but
    starts from the 'expr' rule instead of 'start'.
    """
    global _interpolation_parser
    if _interpolation_parser is None:
        grammar_path = Path(__file__).parent.parent.parent.parent / "grammar.lark"
        from internals.indenter import LangIndenter
        _interpolation_parser = Lark.open(
            str(grammar_path),
            start='expr',
            parser='lalr',
            propagate_positions=True,
            maybe_placeholders=False,
            postlex=LangIndenter(),
            lexer='basic'
        )
    return _interpolation_parser


def apply_location_offset(node: object, base_span: 'Span', visited: set = None) -> None:
    """Recursively adjust locations in AST nodes parsed from interpolation expressions.

    When we parse interpolation expressions like {varname}, the Lark parser
    creates AST nodes with locations relative to the expression string (e.g., 1:1).
    This function walks the AST and adjusts those locations by adding the offset
    from base_span to get the actual file position.

    Args:
        node: An AST node (Expr, Stmt, or any other AST object)
        base_span: The span indicating where the expression starts in the file
        visited: Set of visited object ids to prevent infinite recursion
    """
    from semantics.ast import Node
    from internals.report import Span

    if visited is None:
        visited = set()

    node_id = id(node)
    if node_id in visited:
        return

    if isinstance(node, (str, int, float, bool, type(None))):
        return

    visited.add(node_id)

    line_offset = base_span.line - 1
    col_offset = base_span.col - 1

    if isinstance(node, Node) and hasattr(node, 'loc') and node.loc is not None:
        old_loc = node.loc
        node.loc = Span(
            line=old_loc.line + line_offset,
            col=old_loc.col + col_offset,
            end_line=old_loc.end_line + line_offset,
            end_col=old_loc.end_col + col_offset
        )

    if isinstance(node, Node) and hasattr(node, '__dict__'):
        for attr_name, attr_value in node.__dict__.items():
            if attr_name == 'loc':
                continue
            if isinstance(attr_value, list):
                for item in attr_value:
                    apply_location_offset(item, base_span, visited)
            elif isinstance(attr_value, Node):
                apply_location_offset(attr_value, base_span, visited)


def parse_interpolation_expr(expr_text: str, ast_builder: 'ASTBuilder', fallback_span: 'Span') -> 'Expr':
    """Parse an interpolation expression string into an AST expression node.

    Args:
        expr_text: The expression text from inside {braces}
        ast_builder: The AST builder instance for converting parse trees to AST
        fallback_span: Span to use for fixing locations of nodes without proper locations

    Returns:
        An Expr AST node representing the parsed expression

    Raises:
        Exception: If the expression cannot be parsed
    """
    parser = get_interpolation_parser()
    try:
        tree = parser.parse(expr_text)
        expr_ast = ast_builder._expr(tree)
        apply_location_offset(expr_ast, fallback_span)
        return expr_ast
    except Exception as e:
        raise Exception(f"Failed to parse interpolation expression '{expr_text}': {e}")


def parse_string_token(tok: Token, ast_builder: 'ASTBuilder') -> Union['StringLit', 'InterpolatedString']:
    """Parse a STRING or CHAR_STRING token into either StringLit or InterpolatedString.

    Behavior:
    - STRING (double quotes): Supports interpolation with {expr} syntax
    - CHAR_STRING (single quotes): Plain string literals, no interpolation
    - Both support identical escape sequences

    Args:
        tok: Lark token of type STRING or CHAR_STRING
        ast_builder: AST builder instance for parsing interpolation expressions

    Returns:
        StringLit for plain strings or InterpolatedString for double-quote strings with {expr}
    """
    from semantics.ast import StringLit, InterpolatedString, Name
    from internals.report import span_of

    raw_value = str(tok.value)
    unquoted_value = raw_value[1:-1]  # Strip opening and closing quotes
    span = span_of(tok)

    # Only STRING (double-quote) tokens support interpolation
    is_interpolation_capable = (tok.type == 'STRING')

    if is_interpolation_capable and '{' in unquoted_value:
        # Double-quote string with interpolation expressions
        parts, expr_spans = parse_interpolated_string(unquoted_value, span)

        ast_parts: List[Union[str, Expr]] = []
        expr_index = 0

        for i, part in enumerate(parts):
            if i % 2 == 0:
                # String literal part
                processed_string = process_string_escapes(part)
                ast_parts.append(processed_string)
            else:
                # Expression part
                expr_text = part
                expr_span = expr_spans[expr_index]
                expr_index += 1

                if ast_builder is not None:
                    expr_ast = parse_interpolation_expr(expr_text.strip(), ast_builder, expr_span)
                    ast_parts.append(expr_ast)
                else:
                    ast_parts.append(Name(id=expr_text.strip(), loc=expr_span))

        return InterpolatedString(parts=ast_parts, loc=span)
    else:
        # Plain string literal (no interpolation)
        # Apply escape sequence processing for both STRING and CHAR_STRING
        string_value = process_string_escapes(unquoted_value)
        return StringLit(value=string_value, loc=span)
