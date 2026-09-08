"""Every generic instantiation a WRITTEN type names, over the one type walk.

A type names an instantiation in more places than a hand-written recursion remembers.
The collection here had an arm for an array, a struct and an enum, and none for a
reference, a pointer or a function type, so `fn take(peek Box@(string) b)` answered
CE2001 for a type declared in the same file (#603). `type_walk.walk_named_types` is the
walk CLAUDE.md names for this question; this module is its one node handler on the
collection side.
"""
from __future__ import annotations

from typing import Optional, Set, Tuple, TYPE_CHECKING

from sushi_lang.semantics.generics.types import GenericTypeRef
from sushi_lang.semantics.type_walk import walk_named_types

if TYPE_CHECKING:
    from sushi_lang.semantics.type_resolution import TypeResolver
    from sushi_lang.semantics.typesys import Type


def collect_type_instantiations(
    ty: Optional["Type"],
    resolver: "TypeResolver",
    instantiations: Set[Tuple[str, Tuple["Type", ...]]],
    *,
    structs: Optional[dict] = None,
    enums: Optional[dict] = None,
    sites: Optional[dict] = None,
    site=None,
    file: Optional[str] = None,
    visited: Optional[Set[str]] = None,
) -> None:
    """Add every instantiation `ty` names to `instantiations`.

    `structs` and `enums` let a bare name reach its declaration and be walked through.
    `sites` keeps the FIRST written site per instantiation, so a constraint violation can
    point at it (#579). `visited` stops the walk of an instantiation's RESOLVED arguments
    from re-entering a type that holds itself.
    """
    from sushi_lang.semantics.generics.extension_targets import instantiation_key

    if visited is None:
        visited = set()

    for inner in walk_named_types(ty, structs, enums):
        if not isinstance(inner, GenericTypeRef):
            continue

        resolved_type_args = resolver.resolve_type_args(inner.type_args)
        if resolver.contains_unresolvable_in_tuple(resolved_type_args):
            continue

        instantiations.add((inner.base_name, resolved_type_args))
        key = instantiation_key(inner.base_name, resolved_type_args)
        if sites is not None and site is not None:
            sites.setdefault(key, (site, file))

        if key in visited:
            continue
        visited.add(key)
        # The WRITTEN arguments are on this walk already. A RESOLVED one can name a type
        # the written form does not -- a bare name the resolver stands in for -- so it
        # gets a walk of its own.
        for arg in resolved_type_args:
            collect_type_instantiations(
                arg, resolver, instantiations, structs=structs, enums=enums,
                sites=sites, site=site, file=file, visited=visited)
