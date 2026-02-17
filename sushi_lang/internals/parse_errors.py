"""Shared parse exception handling for loader and CLI."""
from __future__ import annotations

import sys

from lark import UnexpectedInput

from sushi_lang.semantics.ast_builder import (
    BlankReturnSyntaxError,
    UnterminatedInterpolationError,
    EmptyInterpolationError,
    CStyleOctalError,
)


def handle_parse_exception(exc: Exception, reporter, source_path=None) -> bool:
    """Handle a parse exception by emitting diagnostics through the reporter.

    Args:
        exc: The exception to handle.
        reporter: Reporter for error/warning collection.
        source_path: Optional path for context in UnexpectedInput errors.

    Returns:
        True if the exception was handled, False otherwise.
    """
    from sushi_lang.internals import errors as er
    from sushi_lang.internals.parser import improve_parse_error

    if isinstance(exc, BlankReturnSyntaxError):
        er.emit(reporter, er.ERR.CE2036, exc.span)
        return True

    if isinstance(exc, UnterminatedInterpolationError):
        er.emit(reporter, er.ERR.CE2026, exc.span)
        return True

    if isinstance(exc, EmptyInterpolationError):
        er.emit(reporter, er.ERR.CE2038, exc.span)
        return True

    if isinstance(exc, CStyleOctalError):
        octal_value = exc.literal.lstrip('0') or '0'
        er.emit(reporter, er.ERR.CE2071, exc.span, literal=exc.literal, octal=octal_value)
        return True

    if isinstance(exc, UnexpectedInput):
        if source_path:
            print(f"Parse error in {source_path}:", file=sys.stderr)
        print(improve_parse_error(exc), file=sys.stderr)
        return True

    return False
