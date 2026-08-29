"""Which unit a symbol belongs to: the name it takes, and the index that holds it.

Two units may each declare `helper`, so a symbol has to say which unit's declaration it
is (`docs/design/unit-namespaces.md` section 9). `mangle_unit_symbol` is that name, read
by the back end when it declares and by the `.slib` producer when it records
`link_symbol`. `UnitKeyedSymbols` is the index the back end reads it back through.
"""
from __future__ import annotations

from typing import Dict, Generic, Iterator, Mapping, Optional, TypeVar

V = TypeVar("V")

# `$` lies OUTSIDE the alphabet of every other symbol component -- an identifier and a
# sanitized type argument are [A-Za-z0-9_], and the pack marker's separator is "." -- so
# a unit prefix cannot occur in an unprefixed symbol. LLVM accepts it in an identifier.
UNIT_SEP = "$"

# The C entry point. The linker needs the name, and the `entrypoint` pass already
# guarantees one program declares one `main`, so there is nothing to disambiguate.
EXEMPT = frozenset({"main"})


def mangle_unit_symbol(unit_name: Optional[str], name: str) -> str:
    """`<unit>$<name>`, with every `/` in the unit name becoming `$`.

    No unit means no prefix, and only a name that is unique program-wide may arrive
    with none: a lifted lambda (the per-unit lifter's counter, #402) and a generated
    stdlib symbol (one program-wide generator). A monomorphized INSTANCE is not such
    a name -- two units' generics of one name mangle to one base -- so an instance
    arrives WITH its declaring unit and takes its prefix (#495).
    """
    if unit_name is None or name in EXEMPT:
        return name
    return f"{unit_name.replace('/', UNIT_SEP)}{UNIT_SEP}{name}"


def lookup_in_unit(name: str, by_unit: Mapping[str, Mapping[str, V]],
                   flat: Mapping[str, V], unit: Optional[str] = None,
                   scope: object = None) -> Optional[V]:
    """What a bare name means inside one unit. The ONE ladder, over any such pair.

    Three tables are keyed this way -- the collected functions, the collected constants
    and the back end's declared symbols -- and section 8's ladder must read the same in
    all three, or the back end binds a call to a declaration the front end refused.

    With a `UnitScope` the ladder is the whole rule (`semantics/namespaces.py`). Without
    one the reader has no unit scope to ask -- a scratch validator, a table built by
    hand in a test -- and gets the asking unit's own declaration over the flat view,
    which is what the pair meant before a scope existed.
    """
    if scope is not None:
        return scope.resolve(name, by_unit, flat)
    if unit is not None:
        own = by_unit.get(unit)
        if own is not None and name in own:
            return own[name]
    return flat.get(name)


class UnitKeyedSymbols(Generic[V]):
    """Declared symbols keyed by their SUSHI name, in two views.

    The rule that reads them is `FunctionTable.lookup`'s, over whatever the declaration
    produced -- an `ir.Function`, a global constant, a return type, an AST node --
    instead of a `FuncSig`: the asking unit's own declaration answers first, and the flat
    view answers everything else. One rule in the tree, so the back end cannot disagree
    with the collect pass about what a name means inside a unit.

    The flat view is FIRST-wins, which is what the dedup guard in `emit_func_decl` has
    always given it. A symbol that belongs to no unit -- a lifted lambda, an extension
    method, a public library-shipped function -- lives in the flat view alone, and
    `get` is what reads it. A monomorphized instance belongs to its declaring unit
    since #495 and lives in both.
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

    def lookup(self, name: str, unit: Optional[str] = None,
               scope: object = None) -> Optional[V]:
        """What the name means inside `unit`."""
        return lookup_in_unit(name, self.by_unit, self.by_name, unit, scope)

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
