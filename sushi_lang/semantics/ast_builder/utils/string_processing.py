"""String processing utilities for handling escape sequences and interpolation."""
from __future__ import annotations
from typing import List, Tuple, Union, TYPE_CHECKING
from lark import Token

if TYPE_CHECKING:
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.ast import Expr, InterpolatedString, StringLit
    from sushi_lang.semantics.ast_builder.builder import ASTBuilder


def process_string_escapes(raw_string: str) -> str:
    r"""Process escape sequences in a string literal."""
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


def _file_position(raw_string: str, index: int, span: 'Span') -> Tuple[int, int]:
    """The file line and column of `raw_string[index]`; `span` is the whole literal.

    A string literal can go across lines, so a character after a newline counts its
    column from the start of its own line and not from the opening quote.
    """
    before = raw_string[:index]
    newlines = before.count('\n')
    if newlines == 0:
        return span.line, span.col + 1 + index
    return span.line + newlines, index - before.rfind('\n')


def parse_interpolated_string(raw_string: str, span: 'Span') -> Tuple[List[Union[str, str]], List['Span']]:
    """Parse a string with {expression} interpolations."""
    from sushi_lang.internals.diagnostics import SyntaxDiagnostic
    from sushi_lang.internals.report import Span

    parts = []
    expr_spans = []
    current_part = []
    i = 0

    while i < len(raw_string):
        char = raw_string[i]

        if char == '{':
            parts.append(''.join(current_part))
            current_part = []

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
                raise SyntaxDiagnostic("CE2026", span=span)

            expr_content = raw_string[expr_start:i-1]
            if not expr_content.strip():
                raise SyntaxDiagnostic("CE2038", span=span)

            parts.append(expr_content)
            # The hole is parsed with its blank space stripped, so its span starts
            # where the stripped text starts.
            text_start = expr_start + len(expr_content) - len(expr_content.lstrip())
            text_end = expr_start + len(expr_content.rstrip())
            line, col = _file_position(raw_string, text_start, span)
            end_line, end_col = _file_position(raw_string, text_end, span)
            expr_spans.append(Span(line=line, col=col, end_line=end_line, end_col=end_col))

        else:
            current_part.append(char)
            i += 1

    parts.append(''.join(current_part))

    return parts, expr_spans


def apply_location_offset(node: object, base_span: 'Span') -> None:
    """Move every span under `node` by where the interpolation hole starts.

    The nodes come from a second parse of the hole's text alone, so every span they
    carry counts from the hole and not from the file. Every field of every node is
    read through `node_fields`, so a span field added later moves with no change here.
    A span in a list or a tuple field moves, and so does a span on a record a node
    holds (a lambda's `Param`). A typesys `Type` holds no span and is not read.
    """
    from dataclasses import fields, is_dataclass

    from sushi_lang.semantics.ast import Node
    from sushi_lang.internals.report import Span
    from sushi_lang.semantics.ast_walk import node_fields, walk_nodes
    from sushi_lang.semantics.typesys import Type

    line_offset = base_span.line - 1
    col_offset = base_span.col - 1
    # One node or record reached twice would be moved twice. The walk carries no guard
    # of its own, because it is a tree for every other reader; this one keeps its own.
    moved: set[int] = set()

    def moved_span(span: Span) -> Span:
        return Span(
            line=span.line + line_offset,
            col=span.col + col_offset,
            end_line=span.end_line + line_offset,
            end_col=span.end_col + col_offset
        )

    def moved_value(value: object) -> object:
        if isinstance(value, Span):
            return moved_span(value)
        if isinstance(value, (list, tuple)):
            items = [moved_value(item) for item in value]
            if all(new is old for new, old in zip(items, value, strict=True)):
                return value
            return items if isinstance(value, list) else tuple(items)
        if (is_dataclass(value) and not isinstance(value, (type, Node, Type))
                and id(value) not in moved):
            moved.add(id(value))
            move_fields(value, ((f.name, getattr(value, f.name)) for f in fields(value)))
        return value

    def move_fields(owner: object, named_values) -> None:
        for name, value in list(named_values):
            new = moved_value(value)
            if new is not value:
                setattr(owner, name, new)

    def move_the_spans_of(current: Node) -> bool:
        if id(current) in moved:
            return False
        moved.add(id(current))
        move_fields(current, node_fields(current))
        return True

    walk_nodes(node, move_the_spans_of)


def parse_interpolation_expr(expr_text: str, ast_builder: 'ASTBuilder', fallback_span: 'Span') -> 'Expr':
    """Parse an interpolation expression string into an AST expression node."""
    from lark.exceptions import LarkError

    from sushi_lang.internals.diagnostics import SushiError, SyntaxDiagnostic
    from sushi_lang.internals.parse_errors import lark_to_diagnostic

    from sushi_lang.internals.parser import parse_hole

    try:
        tree = parse_hole(expr_text)
        expr_ast = ast_builder._expr(tree)
        apply_location_offset(expr_ast, fallback_span)
        return expr_ast
    except SushiError:
        raise
    except LarkError as e:
        # The interpolation parser's spans are relative to the expression text, not
        # to the file, so the diagnostic points at the interpolation as a whole and
        # carries the parser's own complaint as a note.
        inner = lark_to_diagnostic(e)
        diag = SyntaxDiagnostic("CE6010", span=fallback_span, expr=expr_text)
        for message, _span, _filename in inner.notes:
            diag.note(message)
        raise diag from e


def parse_string_token(tok: Token, ast_builder: 'ASTBuilder') -> Union['StringLit', 'InterpolatedString']:
    """Parse a STRING or CHAR_STRING token into either StringLit or InterpolatedString."""
    from sushi_lang.semantics.ast import StringLit, InterpolatedString, Name
    from sushi_lang.internals.report import span_of

    raw_value = str(tok.value)
    unquoted_value = raw_value[1:-1]  # Strip opening and closing quotes
    span = span_of(tok)

    is_interpolation_capable = (tok.type == 'STRING')

    if is_interpolation_capable and '{' in unquoted_value:
        parts, expr_spans = parse_interpolated_string(unquoted_value, span)

        ast_parts: List[Union[str, Expr]] = []
        expr_index = 0

        for i, part in enumerate(parts):
            if i % 2 == 0:
                processed_string = process_string_escapes(part)
                ast_parts.append(processed_string)
            else:
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
        string_value = process_string_escapes(unquoted_value)
        return StringLit(value=string_value, loc=span)
