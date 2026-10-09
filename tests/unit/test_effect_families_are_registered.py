"""The method-effect table names its families by the registry's handles.

The borrow pass reads a row of `METHOD_EFFECTS` only for a call that one of
`EFFECT_FAMILIES` answered. A name in that set that no family carries would turn the rows
of that family off, and no diagnostic would tell.
"""
from __future__ import annotations

from sushi_lang.semantics.method_effects import EFFECT_FAMILIES
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY


def test_every_effect_family_is_a_registered_family():
    registered = {family.name for family in METHOD_TYPE_REGISTRY.families}
    unknown = sorted(EFFECT_FAMILIES - registered)
    assert not unknown, f"EFFECT_FAMILIES names no registered family: {unknown}"
