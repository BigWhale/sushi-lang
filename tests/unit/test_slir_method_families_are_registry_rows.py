"""Each key of `METHOD_FAMILIES` names a family of `METHOD_TYPE_REGISTRY`.

A misspelled key would never match, and the program would stop at `emit, <family> method`
with no hint (docs/design/ir.md 8.6).
"""
from __future__ import annotations

from sushi_lang.backend.slir import methods
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY


def test_every_family_row_names_a_registry_family():
    unknown = set(methods.METHOD_FAMILIES) - {f.name for f in METHOD_TYPE_REGISTRY._families}
    assert not unknown, f"no family of METHOD_TYPE_REGISTRY is named {sorted(unknown)}"
