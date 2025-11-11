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
        - Track nesting depth of < brackets
        - When depth > 0 and we see RSHIFT, split it into two GT tokens
        - This allows Result<Maybe<i32>> to parse correctly
        - Preserves RSHIFT for shift operators outside generic contexts (e.g., x >> 2)
        """
        for token in stream:
            # Track angle bracket nesting
            if token.type == 'LT':  # <
                self.angle_bracket_depth += 1
                yield token
            elif token.type == 'GT':  # >
                if self.angle_bracket_depth > 0:
                    self.angle_bracket_depth -= 1
                yield token
            elif token.type == 'RSHIFT' and self.angle_bracket_depth > 0:  # >>
                # Split RSHIFT into two GT tokens when inside generic type
                # First >
                yield Token('GT', '>', token.start_pos, token.line, token.column, token.end_line, token.end_column, token.end_pos)
                # Second >
                # Adjust position for second token (one character over)
                yield Token('GT', '>', token.start_pos + 1, token.line, token.column + 1, token.end_line, token.end_column, token.end_pos)

                # Decrease depth by 2 since we're closing two generic levels
                self.angle_bracket_depth = max(0, self.angle_bracket_depth - 2)
            else:
                yield token
