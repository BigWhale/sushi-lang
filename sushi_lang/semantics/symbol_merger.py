"""Symbol table merger for multi-file compilation."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sushi_lang.semantics.tables import SymbolTables
    from sushi_lang.semantics.passes.collect import (
        PerkImplementationTable,
        FunctionTable,
    )


class SymbolTableMerger:
    """Handles merging of symbol tables from multiple compilation units."""

    def merge_all(
        self,
        unit_tables: 'SymbolTables',
        global_tables: 'SymbolTables',
    ) -> None:
        """Merge all symbols from a unit into the global tables."""
        self._merge_constants(unit_tables.constants, global_tables.constants)
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

    @staticmethod
    def _merge_constants(unit_consts, global_consts) -> None:
        """Merge constants, both views. The per-unit half carries across whole.

        The same shape as `_merge_functions`, and for the same reason: a name two units
        declare has one entry per unit, which is what `lookup` gives back.
        """
        SymbolTableMerger._merge_by_name(unit_consts, global_consts)
        for unit_name, declared in unit_consts.by_unit.items():
            global_consts.by_unit.setdefault(unit_name, {}).update(declared)

    def _merge_functions(
        self,
        unit_funcs: 'FunctionTable',
        global_funcs: 'FunctionTable',
    ) -> None:
        """Merge functions (both public and private are tracked)."""
        self._merge_by_name(unit_funcs, global_funcs)

        # The per-unit view carries across whole. It is not first-wins: a name two units
        # declare has one entry per unit, which is the answer `view_for` gives back.
        for unit_name, declared in unit_funcs.by_unit.items():
            global_funcs.by_unit.setdefault(unit_name, {}).update(declared)

        for key, stdlib_func in unit_funcs._stdlib_functions.items():
            if key not in global_funcs._stdlib_functions:
                global_funcs._stdlib_functions[key] = stdlib_func
