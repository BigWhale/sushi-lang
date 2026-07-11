# internals/generic_lexer.py
"""
Custom postlexer for handling >> in nested generic types.

Problem:
--------
When parsing nested generics like Result<Maybe<i32>>, the lexer tokenizes >> as a single
RSHIFT token (for the right-shift operator). This causes parsing to fail because:

1. Parser sees: Result < Maybe < i32 >>
2. Lexer tokens: [Result, <, Maybe, <, i32, RSHIFT]
3. Inner generic (Maybe<i32>) cannot close because RSHIFT is a single atomic token
4. Parser expects: Result < Maybe < i32 > > (two separate > tokens)

Solution:
---------
This postlexer splits RSHIFT (>>) into two GT (>) tokens when inside a generic type context.
We track the nesting depth of < brackets and split >> when depth > 0.

Examples:
---------
- Result<Maybe<i32>>  → works (>> split into > >)
- Result<Maybe<i32> > → works (already has space)
- x >> 2              → works (not inside generics, RSHIFT preserved)

This is the same approach used by C++ parsers to handle nested templates.

Date: 2025-10-16
"""

from lark import Token


class GenericTypeLexer:
    """Postlexer that splits >> tokens into > > for nested generics."""

    def __init__(self):
        self.angle_bracket_depth = 0

    def process(self, stream):
        """
        Process token stream and split RSHIFT (>>) into two GT (>) tokens when inside generics.

        Strategy:
        - Track nesting depth of < brackets, but only count a `<` that opens a
          generic: one written immediately after a type-name token (`List<`),
          not a spaced comparison operator (`a < b`).
        - Reset the depth at every statement boundary (_NEWLINE); a generic type
          never spans a newline, so a stray comparison `<` cannot leak into a
          later line's `>>`.
        - When depth > 0 and we see RSHIFT, split it into two GT tokens so
          Result<Maybe<i32>> parses; otherwise RSHIFT stays a shift operator.
        """
        prev_token = None
        for token in stream:
            # Statement boundary: no generic straddles it, so reset the counter.
            if token.type == '_NEWLINE':
                self.angle_bracket_depth = 0
                prev_token = token
                yield token
                continue

            # Track angle bracket nesting
            if token.type == 'LT':  # <
                # Only a type-name-adjacent `<` opens a generic (no gap between
                # the name and the bracket). A spaced `a < b` is a comparison.
                if (prev_token is not None and prev_token.type == 'NAME'
                        and prev_token.end_pos == token.start_pos):
                    self.angle_bracket_depth += 1
                prev_token = token
                yield token
                continue
            elif token.type == 'GT':  # >
                if self.angle_bracket_depth > 0:
                    self.angle_bracket_depth -= 1
                prev_token = token
                yield token
                continue
            elif token.type == 'RSHIFT' and self.angle_bracket_depth > 0:  # >>
                prev_token = token
                # Split RSHIFT into two GT tokens when inside generic type
                # First >
                yield Token('GT', '>', token.start_pos, token.line, token.column, token.end_line, token.end_column, token.end_pos)
                # Second >
                # Adjust position for second token (one character over)
                yield Token('GT', '>', token.start_pos + 1, token.line, token.column + 1, token.end_line, token.end_column, token.end_pos)

                # Decrease depth by 2 since we're closing two generic levels
                self.angle_bracket_depth = max(0, self.angle_bracket_depth - 2)
            else:
                prev_token = token
                yield token
