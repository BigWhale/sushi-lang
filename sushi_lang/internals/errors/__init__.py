"""Diagnostics: the catalog, and the ways to emit one.

The catalog is split by family, one module per numeric range:

    internal.py  CE0xxx      borrow.py   CE24xx      ffi.py       CE5xxx
    func.py      CE01xx      result.py   CE25xx      syntax.py    CE6xxx
    scope.py     CE1xxx      unit.py     CE3xxx      warnings.py  CWxxxx
    types.py     CE2xxx      library.py  CE35xx      runtime.py   RExxxx
                             perk.py     CE4xxx

Each module owns its range, so a code can only be added in the file that owns it.
Importing this package imports them all, which is what populates REGISTRY.

Callers do `from sushi_lang.internals import errors as er` and reach everything
through `er.ERR.CE2002`, `er.emit(...)`, `er.emit_with(...)`.
"""
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
    """The registry text for a code, formatted.

    The runtime-error emitters use this instead of carrying their own hand-written
    strings, so a code says the same thing wherever it fires.
    """
    return _fmt(code, **kwargs)


def emit(r: Reporter, em: ErrorMessage, span: Optional[Span], **kwargs) -> None:
    text = _fmt(em.code, **kwargs)
    if em.severity == Severity.ERROR:
        r.error(em.code, text, span)
    else:
        r.warn(em.code, text, span)


def emit_with(r: Reporter, em: ErrorMessage, span: Optional[Span], **kwargs) -> DiagnosticBuilder:
    text = _fmt(em.code, **kwargs)
    if em.severity == Severity.ERROR:
        return r.error_with(em.code, text, span)
    return r.warn_with(em.code, text, span)


def emit_exception(r: Reporter, exc: SushiError) -> None:
    """Render a diagnostic that arrived as an exception.

    The single choke point where an exception-borne diagnostic joins the reporter,
    so it lands at the same tier -- text, one location, or one location plus located
    notes -- as an equivalent `emit_with(...)`.
    """
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
    """Raise an InternalCompilerError -- a compiler bug, not a user code issue.

    Args:
        code: Error code (e.g., "CE0013")
        **kwargs: Format parameters for the error message

    Raises:
        InternalCompilerError: always. It subclasses RuntimeError, which is what
            this used to raise.
    """
    raise InternalCompilerError(code, **kwargs)
