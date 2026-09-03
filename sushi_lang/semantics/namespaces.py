"""Where a name may be written: one mechanism, several producers.

`docs/design/unit-namespaces.md` sections 2 and 3 are the rule. A namespace is a binding
from a name to a set of declarations, and two things produce one: an `unsafe external`
block, whose alias is mandatory, and a `use ... as` clause, whose alias is optional. The
FFI boundary had the whole shape years ago and had it alone; this module is that shape
with the FFI table behind it as one producer out of four.

Two seams, in order. This module answers WHERE a name may be written. `visibility.py`
answers WHETHER it may be named at all. So a namespace holds a unit's declarations
whatever their visibility, and a private one is refused at the use site with `CE3005` --
filtering privates out would turn "not yours" into "no such name".

A binding holds a PROVIDER and never a written path (section 3.1). `_inject_library_source`
renames a library's units and leaves `UseStatement.path` alone, so an alias built from the
path works for every user unit and breaks the moment a library unit imports its sibling.
There is no string here for packaging to get wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import (AbstractSet, Any, Dict, Iterable, Mapping, Optional, Tuple,
                    TypeVar)

from sushi_lang.internals.report import Span


V = TypeVar("V")

# The built-in generics a stdlib import ACTIVATES: the type is one per program (Ruling 6)
# and the import is what gives a unit the right to write the name. The membership of one
# `GenericNamespace`, and the whole of what `generics/active_generics.py` used to hold in
# a process-global set five test files had to reset (section 4.3.1).
GENERIC_UNIT_TYPES = {
    "collections/hashmap": "HashMap",
}
GATED_GENERIC_NAMES = frozenset(GENERIC_UNIT_TYPES.values())


@dataclass(frozen=True)
class UnitScope:
    """What ONE unit may write with NO qualifier (section 6).

    A unit sees its own declarations, plus what its own FLAT `use` statements bring.
    Nothing else. An import is not re-exported, so a name a unit next door imported is
    not a name here, and an `as` import contributes nothing at all -- the alias is the
    gate, and `NamespaceTable` beside this is where it lands.

    Three memberships, one per producer that can put a name into the flat scope: a
    compilation unit, a registry stdlib module, and the built-in generic an import
    activates. A declaration that belongs to no unit -- a monomorphized instance, a
    lifted lambda, a binary library's record, every synthesized type -- is in scope
    everywhere, which is the escape `visibility._permitted` takes for the same reason.

    `units` is a TUPLE in `use` order, so which declaration answers is decided the same
    way twice. A name two imports offer is `CE3012` at the use, and the reader that has a
    span to point at is the one that reports it.
    """

    unit: Optional[str] = None
    units: Tuple[str, ...] = ()
    modules: Tuple[str, ...] = ()
    generics: Tuple[str, ...] = ()
    everything: bool = True

    @classmethod
    def unrestricted(cls) -> "UnitScope":
        """The scope of a reader with no unit of its own: a scratch validator."""
        return cls()

    def holds_unit(self, unit_name: Optional[str]) -> bool:
        """May a declaration of `unit_name` be written here without a qualifier?"""
        return (self.everything or unit_name is None
                or unit_name == self.unit or unit_name in self.units)

    def holds_module(self, module_path: Optional[str]) -> bool:
        """May a registry stdlib module's function be written here bare?"""
        return (self.everything or module_path is None
                or module_path in self.modules)

    def holds_generic(self, name: str) -> bool:
        """May a built-in generic be written here? Only a gated one can answer False."""
        return (self.everything or name not in GATED_GENERIC_NAMES
                or name in self.generics)

    def resolve(self, name: str, by_unit: Mapping[str, Mapping[str, V]],
                flat: Mapping[str, V]) -> Optional[V]:
        """Section 8's ladder, rows 2 and 3, over any unit-keyed table.

        The asking unit's own declaration answers first. Then the units a flat `use`
        brought, in the order they were written. A name that some unit declares and no
        import brought is not a name here at all, and only a name no unit declares
        falls through to the flat view -- which is where everything with no unit lives.
        """
        if self.unit is not None:
            own = by_unit.get(self.unit)
            if own is not None and name in own:
                return own[name]
        if self.everything:
            return flat.get(name)
        for unit_name in self.units:
            declared = by_unit.get(unit_name)
            if declared is not None and name in declared:
                return declared[name]
        if any(name in declared for declared in by_unit.values()):
            return None
        return flat.get(name)

    def declaring_units(self, name: str,
                        by_unit: Mapping[str, Mapping[str, V]]) -> Tuple[str, ...]:
        """The units that declare `name` and this one may not write it through."""
        return tuple(unit_name for unit_name, declared in by_unit.items()
                     if name in declared and not self.holds_unit(unit_name))

    def view(self, by_unit: Mapping[str, Mapping[str, V]],
             flat: Mapping[str, V]) -> Dict[str, V]:
        """The same ladder as one mapping, for a reader that wants every name at once."""
        if self.everything:
            merged = dict(flat)
        else:
            owned = {name for declared in by_unit.values() for name in declared}
            merged = {name: value for name, value in flat.items() if name not in owned}
            for unit_name in reversed(self.units):
                merged.update(by_unit.get(unit_name, {}))
        if self.unit is not None:
            merged.update(by_unit.get(self.unit, {}))
        return merged


@dataclass(frozen=True)
class Binding:
    """One name a namespace holds, and what the declaration behind it is.

    `kind` is a word `semantics/ast_walk.declarations()` yields -- "function",
    "constant", "struct", "enum", "perk" -- or "extern" for a foreign function, or
    "type" for a built-in generic an import activates. `record` is whatever the
    producer collected: a `FuncSig`, a `ConstSig`, an `ExternalSig`, a `StdlibFunction`,
    or None where the kind is known and the resolver for it is not built yet.
    """

    kind: str
    name: str
    provider: "Provider"
    record: Any = None

    def ref(self, *, name: Optional[str] = None) -> "NamespaceRef":
        """The stamp a node carries once this binding answered it. The ONE constructor.

        `name` overrides the declared one for a generic whose monomorphized instance is
        what the alias then points at.
        """
        return NamespaceRef(producer=self.provider.namespace_kind,
                            origin=self.provider.origin,
                            name=name or self.name,
                            kind=self.kind)


class Provider:
    """What a namespace binds to. One `lookup`, one `members`, one `origin`.

    `namespace_kind` is the CLOSED set of producers -- "extern", "unit", "stdlib",
    "generic". A resolver reads it to pick the emitter, and the AST carries it beside
    the origin so the back end needs no table of its own.
    """

    namespace_kind: str
    origin: str

    def lookup(self, name: str) -> Optional[Binding]:
        raise NotImplementedError

    def members(self) -> Iterable[str]:
        raise NotImplementedError


class ExternalNamespace(Provider):
    """An `unsafe external "C" as <ns>` block. One kind, and the alias is mandatory."""

    namespace_kind = "extern"

    def __init__(self, external_table: Any, ns: str) -> None:
        self._table = external_table
        self.origin = ns

    def lookup(self, name: str) -> Optional[Binding]:
        sig = self._table.lookup(self.origin, name)
        return None if sig is None else Binding("extern", name, self, sig)

    def members(self) -> Iterable[str]:
        return tuple(self._table.by_namespace.get(self.origin, {}))


class UnitNamespace(Provider):
    """One unit's own declarations, from whichever producer collected them.

    The three views are handed in rather than read out of a `SymbolTables`, because a
    BINARY library has no AST at all: its records arrive from a manifest, and the
    manifest names the unit of every one of them. Both producers hand the same three
    mappings to this one provider.

    `others` carries the kinds a qualified form cannot reach yet -- a struct, an enum
    and a perk. They are members, so an alias over a unit that declares only those is
    not empty, and the grammar is what still refuses to write them (section 5 is the
    phase that lifts it).
    """

    namespace_kind = "unit"

    def __init__(self, origin: str, *, functions: Mapping[str, Any],
                 constants: Mapping[str, Any],
                 generics: Optional[Mapping[str, Any]] = None,
                 others: Optional[Mapping[str, str]] = None) -> None:
        self.origin = origin
        self._functions = functions
        self._constants = constants
        self._generics = generics or {}
        self._others = others or {}

    def lookup(self, name: str) -> Optional[Binding]:
        sig = self._functions.get(name)
        if sig is not None:
            return Binding("function", name, self, sig)
        sig = self._constants.get(name)
        if sig is not None:
            return Binding("constant", name, self, sig)
        generic = self._generics.get(name)
        if generic is not None:
            return Binding("generic function", name, self, generic)
        kind = self._others.get(name)
        return None if kind is None else Binding(kind, name, self)

    def members(self) -> Iterable[str]:
        return (*self._functions, *self._constants, *self._generics, *self._others)


class StdlibNamespace(Provider):
    """A registry stdlib module. Already keyed by `(module, name)` -- section 1.4."""

    namespace_kind = "stdlib"

    def __init__(self, module_path: str, module: Any) -> None:
        self.origin = module_path
        self._module = module

    def lookup(self, name: str) -> Optional[Binding]:
        func = self._module.functions.get(name)
        if func is not None:
            return Binding("function", name, self, func)
        const = self._module.constants.get(name)
        return None if const is None else Binding("constant", name, self, const)

    def members(self) -> Iterable[str]:
        return (*self._module.functions, *self._module.constants)


class GenericNamespace(Provider):
    """A built-in generic that an import activates, such as `<collections/hashmap>`.

    The import brings the name, so the namespace holds it. The type itself is one per
    program (Ruling 6), and what a namespace decides is the right to write the name.
    """

    namespace_kind = "generic"

    def __init__(self, module_path: str, names: AbstractSet[str]) -> None:
        self.origin = module_path
        self._names = frozenset(names)

    def lookup(self, name: str) -> Optional[Binding]:
        return Binding("type", name, self) if name in self._names else None

    def members(self) -> Iterable[str]:
        return tuple(sorted(self._names))


@dataclass(frozen=True)
class NamespaceRef:
    """What the typecheck pass resolved a qualified name to, for the back end.

    Four facts and no lookup: which PRODUCER answered, the unit or module it named,
    the declared name, and the KIND of declaration. The back end routes on `kind` --
    a function is a call through `origin`, a struct is a construction, a constant is a
    load -- so nothing downstream has to guess from the shape of the node.
    """

    producer: str
    origin: str
    name: str
    kind: str


@dataclass(frozen=True)
class _Bound:
    """What one alias binds, and where the clause that bound it stands."""

    provider: Provider
    loc: Optional[Span]


class NamespaceTable:
    """Every namespace ONE unit binds, and what it may write with no qualifier.

    Both halves of "where a name may be written" for one unit: `bind` holds what is
    reachable behind a dot, and `scope` holds what is reachable bare. They are one
    object because every reader of the first needs the second, and because one `use`
    statement contributes to exactly one of them -- `as` is the gate.
    """

    def __init__(self, scope: Optional[UnitScope] = None) -> None:
        self._bound: Dict[str, _Bound] = {}
        self.scope: UnitScope = scope if scope is not None else UnitScope.unrestricted()

    def bind(self, alias: str, provider: Provider, loc: Optional[Span] = None) -> None:
        """Bind a namespace. The caller has already refused a collision (CE3013)."""
        self._bound[alias] = _Bound(provider, loc)

    def is_namespace(self, alias: str) -> bool:
        """True if `alias` names a namespace in this unit."""
        return alias in self._bound

    def lookup(self, alias: str, name: str) -> Optional[Binding]:
        """What `alias.name` denotes, or None if the namespace does not hold it."""
        bound = self._bound.get(alias)
        return None if bound is None else bound.provider.lookup(name)

    def members(self, alias: str) -> Iterable[str]:
        """Every name the namespace holds, for a "did you mean" line."""
        bound = self._bound.get(alias)
        return () if bound is None else bound.provider.members()

    def origin(self, alias: str) -> Optional[str]:
        """The unit or module a diagnostic names. Nothing resolves through it."""
        bound = self._bound.get(alias)
        return None if bound is None else bound.provider.origin

    def bound_at(self, alias: str) -> Optional[Span]:
        """Where the clause that bound this alias stands, for CE3013's note."""
        bound = self._bound.get(alias)
        return None if bound is None else bound.loc


def suggest_member(members: Iterable[str], written: str) -> Optional[str]:
    """The member a misspelt name most likely meant, or None when none is close.

    One reader for every position that can miss: a call, a value and a type all quote
    the same list. The measure is `difflib`'s ratio at its usual 0.6 cut-off, which is
    what a "did you mean" line is worth -- a namespace holds tens of names, not
    thousands, so the cost of ranking them all is nothing.
    """
    from difflib import get_close_matches
    matches = get_close_matches(written, tuple(members), n=1)
    return matches[0] if matches else None


def import_help(origin: str, *, stdlib: bool = False) -> str:
    """The line that names the import an out-of-scope name needs (section 6.1).

    Section 6's refusal is an ordinary "no such name", because that is what it is: the
    name is not in this unit's scope. This line is what turns the refusal into a fix,
    and it is one line for every position, so a call, a type and a bare read all say
    the same thing.
    """
    written = f"<{origin}>" if stdlib else f'"{origin}"'
    return f"'{origin}' declares it; add `use {written}` above to name it here"


def externals_only(external_table: Any) -> NamespaceTable:
    """The FFI namespaces alone, for a reader with no unit of its own.

    `ExternalTable` is flat and has always answered a `libc.printf` from any unit, so
    every unit's table carries every FFI namespace. A scratch validator -- the one that
    walks a monomorphized extension body -- has no unit and gets this.
    """
    table = NamespaceTable()
    for ns in getattr(external_table, "by_namespace", {}):
        table.bind(ns, ExternalNamespace(external_table, ns))
    return table
