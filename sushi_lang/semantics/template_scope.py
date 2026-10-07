"""The scratch layer that a template check reads its body in (#1070).

A template body is checked one time, where it is written, with each type parameter
opaque. The check copy names instances over an opaque parameter -- `List<T#main.f>`, a
`Result@(T, E)`, a tuple, a user `Box@(T)` -- and a program table must never hold one
(CE0148). So the check reads the program through an OVERLAY: a read goes through to the
program table, and a write stays in the overlay. Nothing holds the overlay after the
check, so every instance and every stamp it made is discarded with it.
"""
from __future__ import annotations

import dataclasses
from collections import ChainMap
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from sushi_lang.internals.report import Reporter
from sushi_lang.semantics.derived_methods import OverlayDerivedMethods
from sushi_lang.semantics.passes.collect import EnumTable, FunctionTable, StructTable

if TYPE_CHECKING:
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer
    from sushi_lang.semantics.tables import SymbolTables


def _overlay(base: Any) -> Any:
    """A mapping whose reads go through to `base` and whose writes stay here."""
    return cast(Any, ChainMap({}, base))


def overlay_structs(base: StructTable) -> StructTable:
    """The struct table of a template check: the program's, under a scratch layer."""
    return StructTable(by_name=_overlay(base.by_name), order=[],
                       spans=_overlay(base.spans), files=_overlay(base.files),
                       admits_opaque=True)


def overlay_enums(base: EnumTable) -> EnumTable:
    """The enum table of a template check, with derived methods of its own."""
    return EnumTable(by_name=_overlay(base.by_name), order=[],
                     spans=_overlay(base.spans), files=_overlay(base.files),
                     derived=OverlayDerivedMethods(base.derived), admits_opaque=True)


@dataclass
class TemplateScope:
    """The overlay tables of one template check, and the monomorphizer that cuts in them.

    The monomorphizer only BUILDS instances: its reporter is a throwaway and it has no
    constraint validator, because the typecheck pass judges every written type again,
    with a span. Its caches are its own; an instance the program table holds is found
    there and never built again.
    """

    tables: 'SymbolTables'
    monomorphizer: 'Monomorphizer'
    funcs: FunctionTable
    reporter: Reporter

    def intern(self, ty) -> None:
        """The late interner over the overlay: one seam, a second table set."""
        from sushi_lang.semantics.generics.late_interning import intern_generic_type_refs
        intern_generic_type_refs(self.tables, self.monomorphizer, self.reporter, (ty,))


def template_scope(tables: 'SymbolTables') -> TemplateScope:
    """A fresh overlay over the program tables, for the check of one template.

    Every table that a check copy writes is a scratch one: the struct and enum tables
    (instances and their derived methods), the late interner, and the extension queues.
    A late function request is not made at all. Every other table is the program's, and
    the check copy only reads it.
    """
    from sushi_lang.semantics.generics.monomorphize import Monomorphizer

    reporter = Reporter()
    funcs = FunctionTable()
    overlay = dataclasses.replace(
        tables, structs=overlay_structs(tables.structs), enums=overlay_enums(tables.enums),
        request_function_instance=None, pending_extension_instantiations=[],
        queued_extension_keys=set(), refused_extension_keys=set())
    monomorphizer = Monomorphizer(
        reporter=reporter, constraint_validator=None,
        generic_funcs=tables.generic_funcs,
        generic_enums=tables.generic_enums.by_name,
        generic_structs=tables.generic_structs.by_name,
        func_table=funcs, enum_table=overlay.enums, struct_table=overlay.structs,
        tables=overlay, sites={})
    scope = TemplateScope(overlay, monomorphizer, funcs, reporter)
    overlay.intern_generic_ref = scope.intern
    return scope
