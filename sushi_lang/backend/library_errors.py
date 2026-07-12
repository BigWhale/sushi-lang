"""The exception raised by everything that reads, resolves, or links a .slib.

It lives on its own because library_format.py used to reach into the *linker* for
it -- a reader importing from a linker, which is backwards. Nothing here knows what
a linker is.
"""
from __future__ import annotations

from sushi_lang.internals.errors import ERR


class LibraryError(Exception):
    """Base exception for library-related errors."""

    def __init__(self, code: str, **kwargs):
        self.code = code
        self.kwargs = kwargs
        msg = ERR[code]
        self.message = msg.text.format(**kwargs)
        super().__init__(f"{code}: {self.message}")
