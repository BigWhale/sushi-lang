"""A SHIR pattern as it is written (docs/design/ir.md 7.8, 8.8).

ONE spelling for two levels: a diagnostic of the typecheck pass quotes a pattern with it
(CE2075, CE2118, CE2125, CE2126), and SLIR lowering gives it to the trap RE2023 after the
last arm of a match. A literal and each bound of a range keep their source text (ruling
R18), so the spelling is the text that the source holds. A `Variant` names its enum and a
binding names its local by an id, so the caller gives the written name of each.
"""
from __future__ import annotations

from typing import Callable, Optional

from sushi_lang.internals.errors import raise_internal_error
from sushi_lang.semantics.shir import patterns as p
from sushi_lang.semantics.shir import stmts as s
from sushi_lang.semantics.shir.ids import DeclId, LocalId

#: The written name of a declaration, and of a local.
DeclName = Callable[[DeclId], str]
LocalName = Callable[[LocalId], Optional[str]]


def spell_pattern(pattern: p.ShirPattern | p.PatBinding, decl_name: DeclName,
                  local_name: LocalName) -> str:
    """A pattern as it is written."""
    def spell(item: p.ShirPattern | p.PatBinding) -> str:
        return spell_pattern(item, decl_name, local_name)

    if isinstance(pattern, p.Wildcard):
        return "_"
    if isinstance(pattern, p.Literal):
        return pattern.written
    if isinstance(pattern, p.IntRange):
        return spell_range(pattern)
    if isinstance(pattern, p.Or):
        return " | ".join(spell(alt) for alt in pattern.alternatives)
    if isinstance(pattern, p.Variant):
        head = f"{decl_name(pattern.decl)}.{pattern.variant}"
        if not pattern.items:
            return head
        return f"{head}({', '.join(spell(item) for item in pattern.items)})"
    if isinstance(pattern, p.Tuple):
        return f"({', '.join(spell(item) for item in pattern.elements)})"
    if isinstance(pattern, p.Own):
        return f"Own({spell(pattern.inner)})"
    if isinstance(pattern, p.PatBinding):
        return _spell_binding(pattern, local_name(pattern.local) or "_")
    raise_internal_error("CE0000", span=getattr(pattern, "loc", None),
                         detail=f"no spelling of a {type(pattern).__name__} pattern")
    return ""


def _spell_binding(binding: p.PatBinding, name: str) -> str:
    """A binding with its written mode: `x`, `poke x`, `nom x`."""
    if isinstance(binding.by, s.Ref):
        return f"{binding.by.mode.value} {name}"
    if isinstance(binding.by, p.Nom):
        return f"nom {name}"
    return name


def spell_range(pattern: p.IntRange) -> str:
    """A range as it is written: `0x80..=0x8F`."""
    operator = "..=" if pattern.inclusive else ".."
    return f"{pattern.low.written}{operator}{pattern.high.written}"
