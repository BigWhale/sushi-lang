"""The typecheck arm of a method call chooses the arm of a built-in family from ONE table.

`FAMILY_ARMS` is keyed by the name of a family of `METHOD_TYPE_REGISTRY`, and each family
has its row but `foreign_ptr`: a `ptr` receiver goes to its own arm before the ladder
(`check_foreign_ptr_call`), which gives CE5011 too. A family with no row is an internal
error. No arm compares a family name of its own.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics.method_effects import FOREIGN_PTR_FAMILY
from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.passes.types.visit import calls


def test_every_family_arm_names_a_registry_family():
    assert calls.FAMILY_ARMS, "the typecheck pass has no family arm"
    unknown = set(calls.FAMILY_ARMS) - {family.name for family in METHOD_TYPE_REGISTRY.families}
    assert not unknown, f"no family of METHOD_TYPE_REGISTRY is named {sorted(unknown)}"


def test_each_family_but_foreign_ptr_has_an_arm():
    families = {family.name for family in METHOD_TYPE_REGISTRY.families}
    assert FOREIGN_PTR_FAMILY in families
    assert set(calls.FAMILY_ARMS) == families - {FOREIGN_PTR_FAMILY}


def test_the_method_call_arm_compares_no_family_name():
    source = inspect.getsource(calls)
    assert "family.name ==" not in source, (
        "the typecheck pass chooses a family arm by a comparison, not by FAMILY_ARMS")
