"""Diagnostics that travel as exceptions."""
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
    """A compiler bug: the user's program cannot be blamed for this."""

    code = "CE0000"


class AstBuilderICE(InternalCompilerError):
    """The grammar produced a parse-tree shape the AST builder cannot build."""


class StdlibBuildError(InternalCompilerError):
    """A generator under `sushi_stdlib/src/**` failed to build the standard library."""
