"""The written name of each `DeclId` that lowering gives (docs/design/ir.md 6.3).

Lowering gives a `DeclId` to each declaration that it names, and this table keeps the
written name of the id, so that the printer and the SLIR lowering can show it. The ids
are given in the order of the calls, as every SHIR id is (ir.md 11.3).

A body takes a new id (`declare`). A name that a body refers to (a constant, a `var`, a
function, a type) takes one id for each written name (`refer`), so two references to one
declaration share an id. A reference id is not the id of the body that it names.

The declaration table (`declarations`, `semantics/decl_table.py`) is a different record:
it is made after the analysis, from the AST and the symbol tables, and its entry of a
function or a method keeps the `DeclId` that `declare` gave to its body. It keeps no
reference id.
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
    _referred: dict[str, DeclId] = field(default_factory=dict, init=False, repr=False)

    def declare(self, name: str) -> DeclId:
        """A new id for the declaration that has the written name `name`."""
        decl = self._ids.allocate()
        self._names.append(name)
        return decl

    def refer(self, name: str) -> DeclId:
        """The id of the declaration that a reference names by the written name `name`."""
        decl = self._referred.get(name)
        if decl is None:
            decl = self._referred[name] = self.declare(name)
        return decl

    def name_of(self, decl: DeclId) -> str:
        return self._names[decl.index]
