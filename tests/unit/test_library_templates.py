"""Unit tests for the .slib generic-template codec (Phase 2, P2-T1 + P2-T2)."""
from __future__ import annotations

from sushi_lang.semantics.library_templates import (
    impl_method_symbol,
)


# C4a: concrete perk-impl shipping (templates.perk_impls)


def test_impl_method_symbol_matches_extension_naming():
    """The manifest symbol matches the backend's extension-method mangling
    (extension_symbol): sanitized type name + "_" + method name.
    """
    assert impl_method_symbol("i32", "doubled") == "i32_doubled"
    assert impl_method_symbol("Box<i32>", "unwrap") == "Box__i32_unwrap"
    assert impl_method_symbol("HashMap<string, i32>", "get") == "HashMap__string_i32_get"
