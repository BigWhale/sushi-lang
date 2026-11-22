"""SHIR to SLIR: the symbol of a free function, a method or a static (docs/design/ir.md 8.2, 8.6).

A declaration of the build has the symbol of its declaring unit, which its callee row
carries.
"""
from __future__ import annotations

from typing import Callable, Optional

from sushi_lang.semantics.shir.typeck import ResolvedCall


def callee_symbol(resolved: ResolvedCall, in_unit: Callable[[Optional[str]], str]) -> str:
    """The symbol that `in_unit` makes in the unit of the row."""
    return in_unit(resolved.unit)
