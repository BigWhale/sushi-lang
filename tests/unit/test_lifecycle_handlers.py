"""Every composite kind's lifecycle handler must carry BOTH halves."""
from __future__ import annotations

# Importing the two home modules is what performs the registration.
import sushi_lang.backend.destructors  # noqa: F401
import sushi_lang.backend.expressions.memory  # noqa: F401

from sushi_lang.backend.lifecycle import _KINDS, registered_halves


def test_every_kind_has_both_halves():
    halves = registered_halves()
    missing = {
        kind: ("clone", "destroy")
        for kind in _KINDS
        if halves.get(kind) != ("clone", "destroy")
    }
    assert not missing, (
        f"lifecycle kinds with a one-sided (or absent) handler: "
        f"{ {k: halves.get(k, ()) for k in missing} }.\n"
        "Register the missing half where its emitter lives -- a one-sided handler "
        "is a double free or a leak by construction."
    )


def test_no_stray_kinds():
    assert set(registered_halves()) <= set(_KINDS)
