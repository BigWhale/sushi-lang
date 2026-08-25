"""Syntax errors (CE6xxx) -- the parser's own diagnostics."""
from __future__ import annotations

from sushi_lang.internals.errors.registry import (
    Category,
    ErrorMessage,
    Severity,
    _add,
)


# Syntax errors (CE6xxx) -- the parser's own diagnostics
_add(ErrorMessage("CE6001", Severity.ERROR,
    "unexpected token '{token}'",
    Category.SYNTAX, "The parser reached a token that cannot appear here."))

_add(ErrorMessage("CE6002", Severity.ERROR,
    "unexpected character '{char}'",
    Category.SYNTAX, "The character is not part of any Sushi token."))

_add(ErrorMessage("CE6003", Severity.ERROR,
    "unexpected end of file",
    Category.SYNTAX, "The source ended in the middle of a construct."))

_add(ErrorMessage("CE6004", Severity.ERROR,
    "inconsistent indentation: dedent to column {got}, expected column {expected}",
    Category.SYNTAX, "A dedent must return to a column that an enclosing block opened."))

_add(ErrorMessage("CE6005", Severity.ERROR,
    "could not tokenize input",
    Category.SYNTAX, "The lexer failed on this input."))

_add(ErrorMessage("CE6006", Severity.ERROR,
    "malformed numeric literal '{literal}': {reason}",
    Category.SYNTAX, "An underscore groups the digits of a numeric literal. One underscore, "
                     "between two digits -- so not after the base prefix, not doubled, and "
                     "not next to a point or an exponent marker."))

_add(ErrorMessage("CE6010", Severity.ERROR,
    "could not parse the interpolated expression '{expr}'",
    Category.SYNTAX, "The text between {braces} in a string literal must be a valid expression."))

# Documentation blocks (docs/design/documentation.md sections 2 and 7). All three are
# raised from a lexer callback, at lex time, before the AST builder has an opinion.
_add(ErrorMessage("CE6011", Severity.ERROR,
    "a documentation block is opened here and never closed",
    Category.SYNTAX, "A `##:` opens a documentation block, and a `:##` closes it. The "
                     "closer is on the opening line, or it is line-initial. Treating an "
                     "unmatched opener as a comment would let a whole documented API "
                     "vanish from a build with no signal."))

_add(ErrorMessage("CE6012", Severity.ERROR,
    "a documentation block is closed here, but never opened",
    Category.SYNTAX, "A `:##` closes a documentation block that a `##:` opened. The two "
                     "delimiters are asymmetric on purpose: they let the compiler say "
                     "which of the two mistakes was made."))

_add(ErrorMessage("CE6013", Severity.ERROR,
    "a documentation block is opened inside a documentation block",
    Category.SYNTAX, "Documentation blocks do not nest: a block ends at the first closer "
                     "that qualifies. A line-initial `##:` in the interior means the "
                     "enclosing block swallowed the blocks between the two openers. This "
                     "is the signal GCC gives for a `/*` inside a block comment."))

_add(ErrorMessage("CE6101", Severity.ERROR,
    "nested function definitions are not supported",
    Category.SYNTAX, "A function may only be defined at the top level. Use a lambda for a "
                     "local callable."))

_add(ErrorMessage("CE6102", Severity.ERROR,
    "explicit type arguments are only supported on direct function calls",
    Category.SYNTAX, "The `@(...)` type-argument list may appear only on a call to a named "
                     "free function, not on a method call or an indirect call."))
