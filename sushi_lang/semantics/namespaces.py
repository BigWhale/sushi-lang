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
from typing import AbstractSet, Any, Dict, Iterable, Mapping, Optional

from sushi_lang.internals.report import Span


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
    """Every namespace ONE unit binds. An alias is local to the unit that wrote it."""

    def __init__(self) -> None:
        self._bound: Dict[str, _Bound] = {}

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
