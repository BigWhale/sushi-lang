"""The typecheck arm of a method call chooses the arm of a built-in family from ONE table.

`FAMILY_ARMS` is keyed by the name of a family of `METHOD_TYPE_REGISTRY`. A misspelled key
would never match, and the call would stop at `typecheck, Call(method <family>)` with no
hint, so each key is a registry family. No arm compares a family name of its own.
"""
from __future__ import annotations

import inspect

from sushi_lang.semantics.passes.types.method_registry import METHOD_TYPE_REGISTRY
from sushi_lang.semantics.passes.types.visit import calls


def test_every_family_arm_names_a_registry_family():
    assert calls.FAMILY_ARMS, "the typecheck pass has no family arm"
    unknown = set(calls.FAMILY_ARMS) - {family.name for family in METHOD_TYPE_REGISTRY.families}
    assert not unknown, f"no family of METHOD_TYPE_REGISTRY is named {sorted(unknown)}"


def test_the_method_call_arm_compares_no_family_name():
    source = inspect.getsource(calls)
    assert "family.name ==" not in source, (
        "the typecheck pass chooses a family arm by a comparison, not by FAMILY_ARMS")
