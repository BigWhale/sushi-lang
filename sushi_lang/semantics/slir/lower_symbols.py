"""SHIR to SLIR: the symbol of a free function, a method or a static (docs/design/ir.md 8.2, 8.6).

A declaration of the build has the symbol of its declaring unit. An instance of a template of
a binary library has no declaring unit, and its symbol has no unit either. A declaration of a
binary or a hybrid library has the `link_symbol` of the manifest, which its callee row
carries (S26).
"""
from __future__ import annotations

from typing import Callable, Optional

from sushi_lang.semantics.shir.typeck import ResolvedCall


def callee_symbol(resolved: ResolvedCall, in_unit: Callable[[Optional[str]], str]) -> str:
    """The `link_symbol` of the row, else the symbol that `in_unit` makes in its unit."""
    if resolved.link_symbol is not None:
        return resolved.link_symbol
    return in_unit(resolved.unit)
