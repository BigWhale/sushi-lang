"""A SHIR pattern as it is written (docs/design/ir.md 7.8, 8.8).

ONE spelling for two levels: a diagnostic of the typecheck pass quotes a pattern with it
(CE2075, CE2118, CE2125), and SLIR lowering gives it to the trap RE2023 after the last arm of
a match. A literal and each bound of a range keep their source text (ruling R18), so the
spelling is the text that the source holds.
"""
from __future__ import annotations

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.shir import patterns as p


def spell_pattern(pattern: p.ShirPattern | p.PatBinding) -> str:
    """A pattern as it is written."""
    if isinstance(pattern, p.Wildcard):
        return "_"
    if isinstance(pattern, p.Literal):
        return pattern.written
    if isinstance(pattern, p.IntRange):
        return spell_range(pattern)
    if isinstance(pattern, p.Or):
        return " | ".join(spell_pattern(alt) for alt in pattern.alternatives)
    raise_internal_error("CE0000", span=getattr(pattern, "loc", None),
                         detail=f"no spelling of a {type(pattern).__name__} pattern yet")
    return ""


def spell_range(pattern: p.IntRange) -> str:
    """A range as it is written: `0x80..=0x8F`."""
    operator = "..=" if pattern.inclusive else ".."
    return f"{pattern.low.written}{operator}{pattern.high.written}"
