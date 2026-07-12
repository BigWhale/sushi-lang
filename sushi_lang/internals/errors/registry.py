"""The diagnostic registry itself: the types, the store, and the lookup.

The catalog lives in the sibling family modules, one per numeric range, each of
which registers its own codes through `_add`. This module holds no codes.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class Category(str, Enum):
    GENERAL   = "general"
    SCOPE     = "scope"
    FUNC      = "function"
    TYPE      = "type"
    BORROW    = "borrow"
    UNIT      = "unit"
    LIBRARY   = "library"
    PERK      = "perk"
    FFI       = "ffi"
    SYNTAX    = "syntax"
    RUNTIME   = "runtime"
    INTERNAL  = "internal"


@dataclass(frozen=True)
class ErrorMessage:
    code: str
    severity: Severity
    text: str
    category: Category = Category.GENERAL
    doc: str = ""


REGISTRY: Dict[str, ErrorMessage] = {}


def _add(msg: ErrorMessage) -> None:
    if msg.code in REGISTRY:
        raise ValueError(f"duplicate error code {REGISTRY[msg.code]} in {msg}")
    REGISTRY[msg.code] = msg


def _get(code: str) -> ErrorMessage:
    """Look up a code, degrading rather than raising.

    The error machinery must not crash *while reporting an error*. An unregistered
    code is a compiler bug, so it renders as one -- it does not replace the user's
    diagnostic with a traceback. test_error_registry.py is what keeps this honest.
    """
    msg = REGISTRY.get(code)
    if msg is not None:
        return msg
    return ErrorMessage(code, Severity.ERROR,
                        f"unregistered diagnostic '{code}' (this is a compiler bug)",
                        Category.INTERNAL)


class _SafeParams(dict):
    """Renders a missing format key as <missing:key> instead of raising."""

    def __missing__(self, key: str) -> str:
        return f"<missing:{key}>"


def _fmt(code: str, **kwargs) -> str:
    return _get(code).text.format_map(_SafeParams(kwargs))


class _ErrorCatalog:
    def __init__(self, backing: Dict[str, ErrorMessage]) -> None:
        self._registry = backing

    def __getattr__(self, name: str) -> ErrorMessage:
        return _get(name)

    def __getitem__(self, code: str) -> ErrorMessage:
        return _get(code)


ERR = _ErrorCatalog(REGISTRY)
