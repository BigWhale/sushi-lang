"""Shared parse exception handling for loader and CLI."""
from __future__ import annotations

import sys

from lark import UnexpectedInput

from sushi_lang.internals.diagnostics import SushiError


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

    if isinstance(exc, SushiError):
        er.emit_exception(reporter, exc)
        return True

    if isinstance(exc, UnexpectedInput):
        if source_path:
            print(f"Parse error in {source_path}:", file=sys.stderr)
        print(improve_parse_error(exc), file=sys.stderr)
        return True

    return False
