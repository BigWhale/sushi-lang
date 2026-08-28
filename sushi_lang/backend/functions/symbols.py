"""What a function name means inside the unit being emitted."""
from __future__ import annotations

from typing import Dict, Generic, Iterator, Optional, TypeVar

V = TypeVar("V")


class UnitKeyedSymbols(Generic[V]):
    """Declared symbols keyed by their SUSHI name, in two views.

    The rule that reads them is `FunctionTable.lookup`'s, over an `ir.Function` or a
    return type instead of a `FuncSig`: the asking unit's own declaration answers first,
    and the flat view answers everything else. One rule in the tree, so the back end
    cannot disagree with the collect pass about what a name means inside a unit.

    The flat view is FIRST-wins, which is what the dedup guard in `emit_func_decl` has
    always given it. A symbol that belongs to no unit -- a monomorphized instance, a
    lifted lambda, an extension method, a library-shipped function -- lives in the flat
    view alone, and `get` is what reads it.
    """

    def __init__(self) -> None:
        self.by_name: Dict[str, V] = {}
        self.by_unit: Dict[str, Dict[str, V]] = {}

    def declare(self, name: str, value: V, *, unit: Optional[str] = None) -> None:
        """Register one declaration. The ONE insert."""
        self.by_name.setdefault(name, value)
        if unit is not None:
            self.by_unit.setdefault(unit, {})[name] = value

    def declared(self, name: str, unit: Optional[str]) -> Optional[V]:
        """This unit's OWN declaration of the name, with no fall-back.

        What a dedup guard asks: two units each declaring `helper` are two
        declarations, and the flat view holding one must not stand in for the other.
        """
        if unit is None:
            return self.by_name.get(name)
        return self.by_unit.get(unit, {}).get(name)

    def lookup(self, name: str, unit: Optional[str] = None) -> Optional[V]:
        """What the name means inside `unit`."""
        if unit is not None:
            own = self.by_unit.get(unit)
            if own is not None and name in own:
                return own[name]
        return self.by_name.get(name)

    def get(self, name: str) -> Optional[V]:
        """The flat view, for a symbol that belongs to no unit."""
        return self.by_name.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self.by_name

    def __setitem__(self, name: str, value: V) -> None:
        self.by_name[name] = value

    def __getitem__(self, name: str) -> V:
        return self.by_name[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.by_name)

    def copy(self) -> "UnitKeyedSymbols[V]":
        """A shallow copy of both views, for the incremental path's save and restore."""
        other: UnitKeyedSymbols[V] = UnitKeyedSymbols()
        other.by_name = dict(self.by_name)
        other.by_unit = {unit: dict(names) for unit, names in self.by_unit.items()}
        return other
