"""Syntax errors (CE6xxx) -- the parser's own diagnostics.

This module owns its numeric range: a code may only be added in the file that
owns it, which is what makes the grouping structural rather than conventional.
"""
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

_add(ErrorMessage("CE6010", Severity.ERROR,
    "could not parse the interpolated expression '{expr}'",
    Category.SYNTAX, "The text between {braces} in a string literal must be a valid expression."))

_add(ErrorMessage("CE6101", Severity.ERROR,
    "nested function definitions are not supported",
    Category.SYNTAX, "A function may only be defined at the top level. Use a lambda for a "
                     "local callable."))
