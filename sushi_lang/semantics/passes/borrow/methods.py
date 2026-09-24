"""The borrow pass's view of `semantics/method_effects.py`, the ONE method-effect table.

The table sits outside both passes because the typecheck pass reads it too (CE2096).
"""

from __future__ import annotations

from sushi_lang.semantics.method_effects import (
    BULK_WRITE_METHODS,
    CONTAINER_INSERT_METHODS,
    METHOD_EFFECTS,
    MUTATING_METHODS,
    MethodEffect,
    effect_of,
    methods_where,
)

__all__ = [
    "BULK_WRITE_METHODS", "CONTAINER_INSERT_METHODS", "METHOD_EFFECTS",
    "MUTATING_METHODS", "MethodEffect", "effect_of", "methods_where",
]
