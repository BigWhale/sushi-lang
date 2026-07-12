"""Diagnostics that travel as exceptions.

This module imports only `internals/report.py` (for `Span`). It must NOT import
`internals/errors.py`: the catalog imports *this* module in order to render these
exceptions, and `semantics/ast_builder/**` raises them without dragging the
1200-line catalog into the AST builder.

A `SushiError` carries the same ladder as a diagnostic emitted through
`errors.emit_with(...)`:

    tier 1  no span               -> text only
    tier 2  span                  -> text + one primary location
    tier 3  span + located notes  -> text + primary + secondary locations

A note carries its own `filename`, so a secondary location may point into a
different file than the primary one.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sushi_lang.internals.report import Span

Note = Tuple[str, Optional[Span], Optional[str]]


class SushiError(Exception):
    """Base class for every diagnostic raised as an exception."""

    code: str = "CE0000"

    def __init__(self, code: Optional[str] = None, *, span: Optional[Span] = None,
                 filename: Optional[str] = None, **params) -> None:
        super().__init__(code or type(self).code)
        self.code = code or type(self).code
        self.span = span
        self.filename = filename
        self.params = params
        self.notes: List[Note] = []
        self.helps: List[str] = []

    def note(self, message: str, span: Optional[Span] = None,
             filename: Optional[str] = None) -> "SushiError":
        self.notes.append((message, span, filename))
        return self

    def help(self, message: str) -> "SushiError":
        self.helps.append(message)
        return self

    def __str__(self) -> str:
        if self.params:
            args = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
            return f"{self.code} ({args})"
        return self.code


class SyntaxDiagnostic(SushiError):
    """The source could not be parsed, or parses into an unsupported construct."""


class InternalCompilerError(SushiError, RuntimeError):
    """A compiler bug: the user's program cannot be blamed for this.

    Inherits `RuntimeError` so that `raise_internal_error()` keeps raising something
    every existing handler already recognises.
    """

    code = "CE0000"


class AstBuilderICE(InternalCompilerError):
    """The grammar produced a parse-tree shape the AST builder cannot build."""


class StdlibBuildError(InternalCompilerError):
    """A generator under `sushi_stdlib/src/**` failed to build the standard library."""
