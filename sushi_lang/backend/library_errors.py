"""The exception raised by everything that reads, resolves, or links a .slib.

It lives on its own because library_format.py used to reach into the *linker* for
it -- a reader importing from a linker, which is backwards. Nothing here knows what
a linker is.

It is a `SushiError`, so a `LibraryError` that escapes to the top-level guard renders
through the reporter with its own code (via `except SushiError`), exactly like any
other coded diagnostic -- rather than being wrapped into a CE0000 ICE. Sites that
already catch `LibraryError` explicitly still do.
"""
from __future__ import annotations

from typing import Optional

from sushi_lang.internals.diagnostics import SushiError
from sushi_lang.internals.errors import ERR
from sushi_lang.internals.report import Span


class LibraryError(SushiError):
    """A coded diagnostic about reading, resolving, or linking a .slib."""

    def __init__(self, code: str, *, span: Optional[Span] = None,
                 filename: Optional[str] = None, **kwargs):
        super().__init__(code, span=span, filename=filename, **kwargs)

    @property
    def message(self) -> str:
        """The rendered diagnostic text (params substituted into the registry entry)."""
        return ERR[self.code].text.format(**self.params)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
