"""The written name of each `DeclId`, until the declaration table exists (docs/design/ir.md 6.3).

`declarations(program) -> DeclTable` is the second entry point of ir.md 6.3, and no chunk
has built it yet. Until then, lowering gives a `DeclId` to each declaration that it
names, and this table keeps the written name of the id, so that the printer can show it.
The ids are given in the order of the calls, as every SHIR id is (ir.md 11.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sushi_lang.semantics.shir.ids import DeclId, IdAllocator


@dataclass(slots=True)
class DeclNames:
    """One written name for each `DeclId` that `declare` gives."""
    _names: list[str] = field(default_factory=list, init=False, repr=False)
    _ids: IdAllocator[DeclId] = field(default_factory=lambda: IdAllocator(DeclId),
                                      init=False, repr=False)

    def declare(self, name: str) -> DeclId:
        """A new id for the declaration that has the written name `name`."""
        decl = self._ids.allocate()
        self._names.append(name)
        return decl

    def name_of(self, decl: DeclId) -> str:
        return self._names[decl.index]
