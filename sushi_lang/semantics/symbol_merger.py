"""Symbol table merger for multi-file compilation."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.units import Unit
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.passes.collect import (
        PerkImplementationTable,
        FunctionTable,
    )


class SymbolTableMerger:
    """Handles merging of symbol tables from multiple compilation units."""

    def merge_all(
        self,
        unit: 'Unit',
        unit_tables: 'SymbolTables',
        global_tables: 'SymbolTables',
    ) -> None:
        """Merge all symbols from a unit into the global tables."""
        self._merge_by_name(unit_tables.constants, global_tables.constants)
        self._merge_by_name(unit_tables.structs, global_tables.structs)
        self._merge_by_name(unit_tables.enums, global_tables.enums)
        self._merge_by_name(unit_tables.generic_enums, global_tables.generic_enums)
        self._merge_by_name(unit_tables.generic_structs, global_tables.generic_structs)
        self._merge_by_name(unit_tables.perks, global_tables.perks)
        self._merge_perk_impls(unit_tables.perk_impls, global_tables.perk_impls)
        self._merge_functions(unit_tables.funcs, global_tables.funcs)
        self._merge_by_type(unit_tables.extensions, global_tables.extensions)
        self._merge_by_type(unit_tables.generic_extensions, global_tables.generic_extensions)
        self._merge_by_name(unit_tables.generic_funcs, global_tables.generic_funcs)
        self._merge_visibility(unit_tables.visibility, global_tables.visibility)
        self._replace_shadowed_functions(unit, unit_tables, global_tables)

    def _replace_shadowed_functions(
        self,
        unit: 'Unit',
        unit_tables: 'SymbolTables',
        global_tables: 'SymbolTables',
    ) -> None:
        """A consumer unit's own declaration answers the consumer's own call.

        Every other table merges first-wins, and library units merge FIRST, so a name a
        consumer declares over a library's export was skipped and the library's signature
        answered the consumer's call. The linker disagreed: a private function has internal
        linkage, so the consumer's call binds to the consumer's definition (decision 10 of
        `docs/design/visibility.md`). With the same signature nobody noticed; with a
        different one the consumer heard CE2009 about its own call.

        The collect pass has already decided WHICH declaration wins the name -- it refuses
        what may not be replaced (CE0101, CE3011) and warns about what may (CW3002) -- so
        the winner is whatever its shared table holds. This carries that decision across
        the merge and nothing more. Only a unit the consumer wrote may replace: a library
        unit that collides with another library unit is an ordinary duplicate.

        The displaced unit is booked as a LOSER of the name. One table holds one
        declaration per name, so the library's own body would otherwise be measured
        against the consumer's -- the D2 cascade, pointing at code the consumer did not
        write. A loser's body is not measured against the winner's declaration, and the
        library's own compile checked it already.
        """
        if unit.provenance is not None:
            return
        for table, other in ((unit_tables.funcs, unit_tables.generic_funcs),
                             (unit_tables.generic_funcs, unit_tables.funcs)):
            globals_ = (global_tables.funcs if table is unit_tables.funcs
                        else global_tables.generic_funcs)
            other_globals = (global_tables.generic_funcs if table is unit_tables.funcs
                             else global_tables.funcs)
            for name, declared in table.by_name.items():
                if getattr(declared, "unit_name", None) != unit.name:
                    continue
                kept = globals_.by_name.get(name)
                if kept is not None and kept is not declared:
                    globals_.by_name[name] = declared
                    global_tables.visibility.mark_contested(
                        "function", name, getattr(kept, "unit_name", None))
                # The two tables hold one name between them. A concrete declaration that
                # took over a library's generic (`use <collections/iter>` beside a
                # program's own `fn map`) left the generic behind in the global table.
                if name not in other.by_name:
                    displaced = other_globals.by_name.get(name)
                    if displaced is not None:
                        global_tables.visibility.mark_contested(
                            "function", name, getattr(displaced, "unit_name", None))
                    self._forget(other_globals, name)

    @staticmethod
    def _forget(table, name: str) -> None:
        if name in table.by_name:
            del table.by_name[name]
            if name in table.order:
                table.order.remove(name)

    @staticmethod
    def _merge_visibility(unit_table, global_table) -> None:
        """Replay one unit's declarations into the global table.

        `record()` IS the merge: it keeps the first and books every later one as
        contested, which is what a name-keyed merge does plus the loser it has to
        remember. Without this the global table stays empty and every rule that reads it
        answers from absence.
        """
        for origin in unit_table.by_key.values():
            global_table.record(origin)
        for key, units in unit_table.contested.items():
            global_table.contested.setdefault(key, set()).update(units)

    @staticmethod
    def _merge_by_name(unit_table, global_table) -> None:
        """Merge a name-keyed table into the global one (all symbols global)."""
        unit_spans = getattr(unit_table, "spans", None)
        global_spans = getattr(global_table, "spans", None)

        for name, value in unit_table.by_name.items():
            if name not in global_table.by_name:
                global_table.by_name[name] = value
                global_table.order.append(name)
                if unit_spans is not None and global_spans is not None and name in unit_spans:
                    global_spans[name] = unit_spans[name]

    @staticmethod
    def _merge_by_type(unit_table, global_table) -> None:
        """Merge a type-keyed table of per-method dicts (extension methods).

        The inner key is opaque here: a method name for a plain target, and a (method,
        target key) pair for a generic one, where the target key is what tells
        `extend Box@(i32)` from `extend Box@(string)` (#393).
        """
        for type_name, methods in unit_table.by_type.items():
            target = global_table.by_type.setdefault(type_name, {})
            for key, method in methods.items():
                if key not in target:
                    target[key] = method

    def _merge_perk_impls(
        self,
        unit_perk_impls: 'PerkImplementationTable',
        global_perk_impls: 'PerkImplementationTable',
    ) -> None:
        """Merge perk implementations (all perk implementations are global)."""
        for key, impl in unit_perk_impls.implementations.items():
            if key not in global_perk_impls.implementations:
                type_name, perk_name = key
                global_perk_impls.implementations[key] = impl
                global_perk_impls.units[key] = unit_perk_impls.units.get(key)
                if type_name not in global_perk_impls.by_type:
                    global_perk_impls.by_type[type_name] = set()
                global_perk_impls.by_type[type_name].add(perk_name)
                if perk_name not in global_perk_impls.by_perk:
                    global_perk_impls.by_perk[perk_name] = set()
                global_perk_impls.by_perk[perk_name].add(type_name)

    def _merge_functions(
        self,
        unit_funcs: 'FunctionTable',
        global_funcs: 'FunctionTable',
    ) -> None:
        """Merge functions (both public and private are tracked)."""
        self._merge_by_name(unit_funcs, global_funcs)

        for key, stdlib_func in unit_funcs._stdlib_functions.items():
            if key not in global_funcs._stdlib_functions:
                global_funcs._stdlib_functions[key] = stdlib_func
