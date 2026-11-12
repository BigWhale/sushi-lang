"""The units that declare a method of a name: the extension and perk tables of the pass."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sushi_lang.semantics.passes.types import TypeValidator


def declaring_units(validator: TypeValidator, name: str) -> frozenset[Optional[str]]:
    """The units that declare an extension or a perk method of this name.

    The answer reads the name alone, because the receiver type is not known here.
    """
    units: set[Optional[str]] = set()
    for methods in validator.extension_table.by_type.values():
        units.update(record.unit_name for record in methods.get(name, ()))
    for table in validator.generic_extension_table.by_type.values():
        units.update(unit for (method, _key, unit) in table if method == name)
    perks = validator.perk_impl_table
    units.update(perks.units.get(key) for key, impl in perks.implementations.items()
                 if any(m.name == name for m in impl.methods))
    return frozenset(units)
