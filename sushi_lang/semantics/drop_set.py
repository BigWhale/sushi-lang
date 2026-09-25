"""The one semantic reader of the `Drop` set (#791 row 1).

The semantic half of ruling R2a. `owns_resource` takes the set with no default, and
the semantic passes may not import the back end, so they read the set here and the
back end reads it through `backend/ownership.py:drops_of`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.collect.perks import PerkImplementationTable


def drop_type_names(perk_impls: 'PerkImplementationTable') -> frozenset:
    """The type names that implement `Drop`, the second way a type can own something."""
    return frozenset(perk_impls.by_perk.get("Drop", ()))
