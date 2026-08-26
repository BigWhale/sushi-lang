"""Diagnostics: the catalog, and the ways to emit one."""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals.diagnostics import InternalCompilerError, SushiError
from sushi_lang.internals.report import DiagnosticBuilder, Reporter, Span

from sushi_lang.internals.errors.registry import (
    ERR,
    REGISTRY,
    Category,
    ErrorMessage,
    Severity,
    _fmt,
    _get,
)

# Importing each family registers its codes. They are imported for that side
# effect; nothing here references them by name.
from sushi_lang.internals.errors import (  # noqa: F401
    borrow,
    docs,
    ffi,
    func,
    internal,
    library,
    perk,
    result,
    runtime,
    scope,
    syntax,
    types,
    unit,
    warnings,
)

__all__ = [
    "ERR",
    "REGISTRY",
    "Category",
    "ErrorMessage",
    "Severity",
    "emit",
    "emit_exception",
    "emit_with",
    "message_for",
    "raise_internal_error",
]


def message_for(code: str, **kwargs) -> str:
    """The registry text for a code, formatted."""
    return _fmt(code, **kwargs)


def emit(r: Reporter, em: ErrorMessage, span: Optional[Span],
         filename: Optional[str] = None, **kwargs) -> None:
    """Emit a diagnostic. `filename` overrides the reporter's file."""
    text = _fmt(em.code, **kwargs)
    if em.severity == Severity.ERROR:
        r.error(em.code, text, span, filename=filename)
    else:
        r.warn(em.code, text, span, filename=filename)


def emit_with(r: Reporter, em: ErrorMessage, span: Optional[Span],
              filename: Optional[str] = None, **kwargs) -> DiagnosticBuilder:
    text = _fmt(em.code, **kwargs)
    if em.severity == Severity.ERROR:
        return r.error_with(em.code, text, span, filename=filename)
    return r.warn_with(em.code, text, span, filename=filename)


def emit_exception(r: Reporter, exc: SushiError) -> None:
    """Render a diagnostic that arrived as an exception."""
    em = _get(exc.code)
    text = _fmt(exc.code, **exc.params)
    if em.severity == Severity.ERROR:
        builder = r.error_with(exc.code, text, exc.span, filename=exc.filename)
    else:
        builder = r.warn_with(exc.code, text, exc.span, filename=exc.filename)

    for message, span, filename in exc.notes:
        builder.note(message, span, filename)
    for message in exc.helps:
        builder.help(message)


def raise_internal_error(code: str, **kwargs) -> None:
    """Raise an InternalCompilerError -- a compiler bug, not a user code issue."""
    raise InternalCompilerError(code, **kwargs)
