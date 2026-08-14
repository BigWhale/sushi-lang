"""Every composite kind's lifecycle handler must carry BOTH halves.

A composite type's deep clone duplicates exactly the heap its destructor frees:
clone fewer buffers -> double free, more -> leak. The two halves register into
`backend/lifecycle.py`'s handler table from their home modules
(backend/destructors.py registers destroy; backend/expressions/memory.py registers
clone). A kind registered on one side only used to be exactly how a missing clone
arm shipped (MM.md Phase 2: "adding either alone trades a double free for a
miscompile") -- this test makes it a red build instead.
"""
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
