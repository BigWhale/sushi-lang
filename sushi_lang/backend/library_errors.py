"""The exception raised by everything that reads, resolves, or links a .slib."""
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
